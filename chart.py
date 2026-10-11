#!/usr/bin/env python3
"""Gráfico PNG de una señal, para adjuntar a la alerta (Discord y Telegram).

Sin costo de tokens y sin servicios externos: se dibuja localmente con Pillow (venv del bot).
Paleta dark tech: fondo #050505, ámbar #FFB000 para lo alcista/estructura, rojo #E5484D para el
riesgo y verde #26C281 para el objetivo.

Reutiliza los indicadores de `signals.py` con import TARDÍO (dentro de la función) para no
duplicar RSI/ATR y no crear un ciclo de imports.
"""
import os
import datetime as dt

from PIL import Image, ImageDraw, ImageFont

W, H = 1280, 830
M_IZQ, M_DER = 14, 214
HEADER_H = 96
PRICE_H = 458
GAP = 26
RSI_H = 176
FOOTER_H = 34
BARS = 120                         # velas de la ventana (TV muestra ~300: esto es el punto medio legible)
BARS_MAX = 170                     # tope (si la divergencia es más vieja, se prioriza mostrarla)

BG = (5, 5, 5)
PANEL = (12, 12, 14)
GRID = (30, 30, 34)
TXT = (232, 232, 228)
TXT_DIM = (140, 140, 148)
AMBER = (255, 176, 0)
RED = (229, 72, 77)
GREEN = (38, 194, 129)
WHITE = (245, 245, 240)
BULL = GREEN      # vela alcista (verde, como TradingView: así se compara a ojo)
BEAR = RED

FONT_DIR = "/usr/share/fonts/truetype/dejavu"
_fonts = {}


def font(size, bold=False):
    key = (size, bold)
    if key in _fonts:
        return _fonts[key]
    nombre = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    try:
        f = ImageFont.truetype(os.path.join(FONT_DIR, nombre), size)
    except Exception:
        try:
            f = ImageFont.load_default(size=size)
        except Exception:
            f = ImageFont.load_default()
    _fonts[key] = f
    return f


def _cls(v):
    return (max(0, min(255, v[0])), max(0, min(255, v[1])), max(0, min(255, v[2])))


def _blend(c, alpha):
    return _cls(tuple(int(BG[i] + (c[i] - BG[i]) * alpha) for i in range(3)))


def _dashed(d, p1, p2, color, width=2, dash=9, gap=7):
    """Línea punteada (PIL no la trae)."""
    x1, y1, x2, y2 = *p1, *p2
    dx, dy = x2 - x1, y2 - y1
    largo = (dx * dx + dy * dy) ** 0.5
    if largo < 1:
        return
    ux, uy = dx / largo, dy / largo
    pos = 0.0
    while pos < largo:
        fin = min(pos + dash, largo)
        d.line([(x1 + ux * pos, y1 + uy * pos), (x1 + ux * fin, y1 + uy * fin)],
               fill=color, width=width)
        pos = fin + gap


def _texto(d, xy, txt, color=TXT, size=17, bold=False, anchor="la"):
    d.text(xy, txt, font=font(size, bold), fill=color, anchor=anchor)


def rsi_serie(closes, period=14):
    """RSI Wilder: se usa el de signals.py si se puede (una sola implementación)."""
    try:
        import signals as S
        return S.rsi_wilder(closes, period)
    except Exception:
        return [None] * len(closes)


def _pivot_k():
    """Velas a cada lado del pivote: el mismo valor que usa signals.py (no duplicar la constante)."""
    try:
        import signals as S
        return int(S.PIVOT_K)
    except Exception:
        return 3


def _ventana(todas, sig, es_div):
    """Recorta la ventana de velas de modo que SIEMPRE entren la divergencia y la vela de entrada.
    Devuelve (velas, índice_inicial) — el índice inicial es imprescindible para recortar el RSI con
    el MISMO desplazamiento (si la ventana no termina al final de la serie, asumir el final desplaza
    los valores y el panel muestra un RSI que no es el de la señal).
    Con señales frescas (las de producción) sale la ventana mínima; con señales viejas se corre
    hacia atrás para no perder los pivotes."""
    fin = len(todas) - 1
    idx = next((i for i, c in enumerate(todas) if int(c[0]) == int(sig.get("entry_ts", -1))), None)
    if idx is not None:
        fin = min(len(todas) - 1, idx + 2)
    ancho = BARS
    if es_div and idx is not None and sig.get("pivot_gap"):
        ini_piv = idx - _pivot_k() - int(sig["pivot_gap"]) - 8
        ancho = min(BARS_MAX, max(BARS, idx - ini_piv + 4))
    ini = max(0, fin - ancho + 1)
    return todas[ini:fin + 1], ini


def _velas(n):
    return "vela" if n == 1 else "velas"


def render_marcador(resultados, out_path, titulo="MARCADOR DEL SERVER", dias=None):
    """Vista de conjunto: una barra por señal cerrada — verde las que llegaron al objetivo,
    rojo las que saltaron el stop, gris las cerradas por tiempo. Para ver de un vistazo qué
    señales salieron mal (y cuánto)."""
    filas = [r for r in resultados if r.get("estado") in ("objetivo", "stop", "por tiempo")]
    if not filas:
        raise ValueError("sin cierres para graficar")
    filas = filas[-40:]                    # últimos 40 cierres
    n = len(filas)
    ancho_barra = max(10, int((W - M_IZQ - M_DER - 40) / n) - 6)
    alto = 620
    img = Image.new("RGB", (W, alto), BG)
    d = ImageDraw.Draw(img, "RGBA")
    x0, y0 = M_IZQ + 20, 120
    y1 = alto - 110
    cero = y0 + (y1 - y0) * 0.62          # línea de cero desplazada: casi todo son pérdidas cortas
    d.line([(x0 - 10, cero), (W - M_DER, cero)], fill=_blend(TXT_DIM, .55), width=1)

    objetivos = [r for r in filas if r["estado"] == "objetivo"]
    stops = [r for r in filas if r["estado"] == "stop"]
    otros = [r for r in filas if r["estado"] == "por tiempo"]
    mov_medio = sum(r["mov_pct"] for r in filas) / n
    _texto(d, (M_IZQ + 6, 16), titulo, TXT, 30, True)
    _texto(d, (M_IZQ + 6, 56),
           f"{n} cierres · {len(objetivos)} al objetivo / {len(stops)} al stop"
           + (f" / {len(otros)} por tiempo" if otros else "")
           + f" · {100 * len(objetivos) / n:.0f}% al objetivo · {mov_medio:+.2f}% por alerta",
           TXT_DIM, 18)
    if dias:
        _texto(d, (W - M_DER - 6, 20), f"últimos {dias} días", TXT_DIM, 16, anchor="ra")

    maximo = max(abs(r["mov_pct"]) for r in filas) or 1
    for i, r in enumerate(filas):
        xb = x0 + i * (ancho_barra + 6)
        color = {"objetivo": GREEN, "stop": RED}.get(r["estado"], TXT_DIM)
        h = (y1 - y0) * 0.36 * abs(r["mov_pct"]) / maximo
        if r["mov_pct"] >= 0:
            d.rectangle([xb, cero - h, xb + ancho_barra, cero], fill=_blend(color, .85))
        else:
            d.rectangle([xb, cero, xb + ancho_barra, cero + h], fill=_blend(color, .85))
        if i % max(1, n // 12) == 0 or i == n - 1:
            _texto(d, (xb + ancho_barra / 2, cero + h + 8 if r["mov_pct"] < 0 else cero - h - 22),
                   r["sym"].replace("USDT", ""), TXT_DIM, 13, anchor="ma")
    _texto(d, (M_IZQ + 6, cero - 22), "0%", TXT_DIM, 12)
    _texto(d, (M_IZQ + 6, 88), "cada barra = una señal cerrada · altura = % de movimiento · "
                              "verde objetivo / rojo stop", TXT_DIM, 14)
    _texto(d, (M_IZQ + 6, alto - 40),
           "Medición del propio server. n chico = ruido: mirá el acumulado, no la barra suelta.",
           TXT_DIM, 15)
    img.save(out_path, "PNG", optimize=True)
    return out_path


def render(sig, candles, out_path, resultado=None):
    """Dibuja el gráfico de una señal. `candles` = klines crudos de Binance (incluye la vela viva).

    `resultado` (opcional, lo pasa `tracker.py` al publicar un cierre) = {"estado", "velas",
    "cierre", "mov_pct"}: se dibuja el recorrido entrada→salida, el marcador del cierre y se
    atenúan las velas posteriores, para que se vea de un vistazo si la señal salió bien o mal."""
    todas = list(candles[:-1])
    # Solo los tipos de señal que SON una divergencia traen pivotes y RSI reales; en momentum y
    # rebote esos campos vienen de relleno (50/50), así que no se dibuja divergencia ninguna.
    es_div = str(sig.get("tipo", "")).lower().startswith("clásica")
    cerradas, ini_ventana = _ventana(todas, sig, es_div)
    # RSI sobre la serie COMPLETA y recién después se recorta la ventana con SU índice inicial:
    # un indicador recursivo calculado sobre la ventana suelta arranca sin calentamiento (valores
    # falsos) y recortarlo con el desplazamiento equivocado muestra el RSI de otras velas.
    rsi = rsi_serie([float(c[4]) for c in todas])[ini_ventana:ini_ventana + len(cerradas)]
    n = len(cerradas)
    if n < 12:
        raise ValueError("muy pocas velas para graficar")

    closes = [float(c[4]) for c in cerradas]
    highs = [float(c[2]) for c in cerradas]
    lows = [float(c[3]) for c in cerradas]
    vols = [float(c[5]) for c in cerradas]
    entry = float(sig.get("entry") or closes[-1])     # precio de entrada de la señal

    # índices de los pivotes (el segundo pivote llega por pivot_ts, el primero por pivot_gap)
    p2 = next((i for i, c in enumerate(cerradas) if int(c[0]) == int(sig.get("pivot_ts", -1))), None) \
        if es_div else None
    p1 = p2 - int(sig.get("pivot_gap") or 0) if p2 is not None else None
    e = next((i for i, c in enumerate(cerradas) if int(c[0]) == int(sig.get("entry_ts", -1))), None)

    # --- ESCALA: se ajusta a las VELAS (como TradingView), con 4% de aire. Antes entraban en la
    # escala los niveles y las bandas aunque estuvieran lejísimos y las velas quedaban aplastadas.
    margen = 0.04
    lo_c, hi_c = min(lows), max(highs)
    rango_v = max(hi_c - lo_c, hi_c * 0.002)
    vmin, vmax = lo_c - rango_v * margen, hi_c + rango_v * margen
    dentro = lambda v: v is not None and vmin <= float(v) <= vmax
    niveles = [v for v in (sig.get("sl"), sig.get("tp"), sig.get("entry"),
                           (sig.get("soportes") or [None])[0], sig.get("max_previo")) if dentro(v)]
    # las bandas se dibujan solo si asoman al rango visible (y se recortan al panel)
    for ob in (sig.get("ob_arriba"), sig.get("ob_abajo")):
        if ob and (vmin <= ob["hi"] and ob["lo"] <= vmax):
            niveles += [max(ob["lo"], vmin), min(ob["hi"], vmax)]
    rango = max(vmax - vmin, vmax * 0.002)
    vmin, vmax = vmin - rango * 0.05, vmax + rango * 0.05

    px_izq, px_der = M_IZQ + 6, W - M_DER
    y0, y1 = HEADER_H, HEADER_H + PRICE_H
    # con resultado, la banda del veredicto ocupa la parte alta del panel: las etiquetas de nivel
    # arrancan debajo para no pisarla
    y_min_etiquetas = y0 + 56 if resultado else y0 + 4

    def x(i):
        return px_izq + (px_der - px_izq) * (i + 0.5) / n

    def y(v):
        return y1 - (y1 - y0) * (v - vmin) / (vmax - vmin)

    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img, "RGBA")

    # --- paneles y grilla
    d.rectangle([px_izq, y0, px_der, y1], fill=PANEL)
    rs0, rs1 = y1 + GAP, y1 + GAP + RSI_H
    d.rectangle([px_izq, rs0, px_der, rs1], fill=PANEL)
    for k in range(5):
        v = vmin + (vmax - vmin) * k / 4
        yy = y(v)
        d.line([(px_izq, yy), (px_der, yy)], fill=GRID, width=1)
        # los precios de la grilla van DENTRO del panel y en gris: la columna derecha es de los niveles
        _texto(d, (px_izq + 6, yy + 3), f"{v:,.4f}".rstrip("0").rstrip("."), TXT_DIM, 14)
    for v in (30, 50, 70):
        yy = rs1 - RSI_H * v / 100
        d.line([(px_izq, yy), (px_der, yy)], fill=(GRID if v != 50 else _blend(TXT_DIM, .35)), width=1)
        _texto(d, (px_der + 8, yy), str(v), TXT_DIM, 14)

    # --- volumen (al pie del panel de precio)
    vmax_vol = max(vols) or 1
    for i, v in enumerate(vols):
        h = 0.16 * PRICE_H * (v / vmax_vol)
        color = _blend(BULL if closes[i] >= float(cerradas[i][1]) else BEAR, .45)
        d.rectangle([x(i) - 2, y1 - h, x(i) + 2, y1], fill=color)

    # --- BANDAS de bloques de orden (FONDO): se dibujan ANTES de las velas para que las velas
    # queden nítidas encima. Relleno fuerte + rayado diagonal = zona de verdad, muy visible.
    def banda_geom(ob):
        """(z_lo, z_hi, yz0, yz1, xz) recortado al panel, o None si no entra."""
        z_lo, z_hi = float(ob["lo"]), float(ob["hi"])
        yz0 = max(y(z_hi), y0 + 1)
        yz1 = min(y(z_lo), y1 - 1)
        if yz1 - yz0 < 3:
            return None
        i_o = next((i for i, c in enumerate(cerradas) if int(c[0]) == int(ob.get("ts") or -1)), None)
        return z_lo, z_hi, yz0, yz1, (x(i_o) if i_o is not None else px_izq)

    for clave, color in (("ob_arriba", RED), ("ob_abajo", GREEN)):
        ob = sig.get(clave)
        g = banda_geom(ob) if ob else None
        if not g:
            continue
        z_lo, z_hi, yz0, yz1, xz = g
        d.rectangle([xz, yz0, px_der, yz1], fill=_blend(color, .42))        # relleno bien visible
        alto_b = yz1 - yz0
        xx = xz                                                             # rayado diagonal
        while xx < px_der:
            x2 = min(xx + alto_b * 0.9, px_der)
            d.line([(xx, yz1), (x2, yz0)], fill=_blend(color, .22), width=1)
            xx += 13
        d.line([(xz, yz0), (xz, yz1)], fill=_blend(color, 1.0), width=3)     # borde de origen
        d.line([(xz, yz0), (px_der, yz0)], fill=_blend(color, .75), width=2)  # techo
        d.line([(xz, yz1), (px_der, yz1)], fill=_blend(color, .75), width=2)  # piso

    # --- velas
    ancho = max(2, int((px_der - px_izq) / n * 0.62))
    for i, c in enumerate(cerradas):
        o, h, l, cl = float(c[1]), float(c[2]), float(c[3]), float(c[4])
        alcista = cl >= o
        color = BULL if alcista else BEAR
        xx = x(i)
        d.line([(xx, y(h)), (xx, y(l))], fill=color, width=2)
        top, bot = y(max(o, cl)), y(min(o, cl))
        d.rectangle([xx - ancho / 2, top, xx + ancho / 2, max(bot, top + 1.5)], fill=color)

    # --- niveles: líneas + etiquetas apiladas (nunca superpuestas)
    etiquetas = []
    base = entry   # los % de los niveles van contra la entrada, como en la alerta

    def nivel(valor, color, nombre, dash=(8, 6), grosor=2):
        if not valor or not (vmin <= float(valor) <= vmax):
            return                        # un nivel fuera del rango visible no se dibuja (ni su rótulo)
        val = float(valor)
        yy = y(val)
        _dashed(d, (px_izq, yy), (px_der, yy), color, grosor, *dash)
        etiquetas.append((yy, nombre, val, (val / base - 1) * 100, color))

    soporte = (sig.get("soportes") or [None])[0]
    nivel(sig.get("sl"), RED, "STOP")
    nivel(sig.get("tp"), GREEN, "OBJETIVO")
    nivel(soporte, AMBER, "SOPORTE", dash=(3, 5), grosor=1)
    nivel(sig.get("max_previo"), AMBER, "MÁX 30d", dash=(3, 5), grosor=1)
    nivel(sig.get("entry"), WHITE, "ENTRADA", dash=(7, 5), grosor=1)

    etiquetas.sort(key=lambda t: t[0])
    xl, alto = px_der + 10, 54
    pos = []
    for yy, *_ in etiquetas:
        pos.append(yy if not pos else max(yy, pos[-1] + alto))
    if pos:
        # si el bloque de etiquetas se pasa del fondo del panel, se corre ENTERO hacia arriba
        # (antes se recortaba cada etiqueta por separado y se aplastaban unas sobre otras)
        sobra = pos[-1] - (y1 - 56)
        if sobra > 0:
            pos = [p - sobra for p in pos]
        for (yy, nombre, val, rel, color), yl in zip(etiquetas, pos):
            yl = max(yl, y_min_etiquetas)
            d.line([(px_der + 1, yy), (px_der + 8, yy)], fill=color, width=3)
            d.line([(px_der + 8, yl + 9), (px_der + 8, yy)], fill=_blend(color, .45), width=1)
            _texto(d, (xl, yl), nombre, color, 16, True)
            _texto(d, (xl, yl + 20), f"{val:,.4f}".rstrip("0").rstrip("."), color, 16, True)
            _texto(d, (xl, yl + 38), f"{rel:+.2f}%", TXT_DIM, 13)

    # (las plaquetas de los bloques se dibujan al FINAL, encima de todo: ver más abajo)

    # --- divergencia
    if p1 is not None and p2 is not None and 0 <= p1 < n:
        for idx, col in ((p1, _blend(AMBER, .75)), (p2, AMBER)):
            d.line([(x(idx) - 7, y(highs[idx])), (x(idx) + 7, y(highs[idx]))], fill=col, width=2)
            d.ellipse([x(idx) - 7, y(highs[idx]) - 7, x(idx) + 7, y(highs[idx]) + 7],
                      outline=col, width=2)
        _dashed(d, (x(p1), y(highs[p1])), (x(p2), y(highs[p2])), RED, 3, 12, 6)
        # (los valores del RSI no se repiten en este panel: ya están junto a cada punto en el panel
        #  del RSI, y dibujarlos acá chocaba con la plaqueta de la banda de VENTA)

    if e is not None:
        _texto(d, (x(e), y1 - 6), "señal", WHITE, 14, False, anchor="ma")
        d.line([(x(e), y0), (x(e), y1)], fill=_blend(WHITE, .33), width=1)

    # --- RSI
    vals = [(i, v) for i, v in enumerate(rsi) if v is not None]
    if vals:
        pts = [(x(i), rs1 - RSI_H * v / 100) for i, v in vals]
        d.line(pts, fill=AMBER, width=2)
        if p2 is not None and p2 < len(rsi):
            for idx in (p1, p2):
                if idx is None or idx >= len(rsi) or rsi[idx] is None:
                    continue
                cx, cy = x(idx), rs1 - RSI_H * rsi[idx] / 100
                d.ellipse([cx - 6, cy - 6, cx + 6, cy + 6], fill=AMBER)
        if p1 is not None and p2 is not None and p1 < len(rsi) and p2 < len(rsi):
            if rsi[p1] is not None and rsi[p2] is not None:
                _dashed(d, (x(p1), rs1 - RSI_H * rsi[p1] / 100),
                        (x(p2), rs1 - RSI_H * rsi[p2] / 100), RED, 3, 10, 5)
        for idx in (p1, p2):
            if idx is None or idx >= len(rsi) or rsi[idx] is None:
                continue
            cx, cy = x(idx), rs1 - RSI_H * rsi[idx] / 100
            _texto(d, (min(cx + 10, px_der - 46), cy - 9), f"{rsi[idx]:.1f}", AMBER, 14, True)
    # leyenda fija del panel: explica el trazo punteado sin pelear por el espacio con los rótulos
    if p1 is not None and p2 is not None and 0 <= p1 < n and 0 <= p2 < n:
        _texto(d, (px_izq + 78, rs0 + 6), "· divergencia bajista entre los dos pivotes marcados",
               _blend(RED, .85), 13, True)
    _texto(d, (px_izq + 8, rs0 + 6), "RSI 14", TXT_DIM, 14, True)
    _texto(d, (px_izq + 8, y1 - int(0.16 * PRICE_H) - 17), "volumen", _blend(TXT_DIM, .85), 12)

    # --- cabecera
    short = sig.get("dir") == "short"
    color_dir = RED if short else GREEN
    fecha = dt.datetime.fromtimestamp(closes and int(cerradas[-1][0]) / 1000 or 0).strftime("%Y-%m-%d %H:%M")
    _texto(d, (M_IZQ + 6, 16), f"{sig.get('sym')}  ·  {sig.get('tf')}", TXT, 30, True)
    etiqueta = ("DIVERGENCIA BAJISTA (short)" if short else "DIVERGENCIA ALCISTA (long)")
    if sig.get("tipo"):
        etiqueta = str(sig["tipo"]).upper()
    _texto(d, (M_IZQ + 6, 54), etiqueta, color_dir, 18, True)
    score = sig.get("score")
    if score:
        _texto(d, (W - M_DER - 6, 14), f"{score}/10", AMBER, 34, True, anchor="ra")
        _texto(d, (W - M_DER - 6, 54), f"{sig.get('score_label') or ''} · {sig.get('tier') or ''}",
               TXT_DIM, 15, anchor="ra")

    # --- resultado del cierre (lo manda el tracker al publicar): recorrido + marcador
    if resultado:
        estado = resultado.get("estado")
        color_res = {"objetivo": GREEN, "stop": RED}.get(estado, TXT_DIM)
        ix_salida = min(n - 1, (e if e is not None else n - 1) + int(resultado.get("velas") or 1))
        # velas posteriores al cierre, atenuadas: el "después" ya no cuenta
        for i in range(ix_salida + 1, n):
            o_, c_ = float(cerradas[i][1]), float(cerradas[i][4])
            col = _blend(TXT_DIM, .55)
            xx = x(i)
            d.line([(xx, y(float(cerradas[i][2]))), (xx, y(float(cerradas[i][3])))], fill=col, width=2)
            d.rectangle([xx - ancho / 2, y(max(o_, c_)), xx + ancho / 2, y(min(o_, c_))], fill=col)
        if e is not None:
            xi, yi = x(e), y(float(entry))
            xs, ys = x(ix_salida), y(float(resultado.get("cierre") or entry))
            d.line([(xi, yi), (xs, ys)], fill=_blend(color_res, .85), width=4)
            d.line([(xs, y0), (xs, y1)], fill=_blend(color_res, .5), width=2)
            # marcador dibujado a mano (los glifos de emoji NO existen en la fuente del gráfico:
            # salían como cuadraditos). Para stop, un aspa; para objetivo, un tilde; para el
            # cierre por tiempo, un punto.
            r_ = 10
            d.ellipse([xs - r_, ys - r_, xs + r_, ys + r_], fill=color_res)
            if estado == "stop":
                d.line([(xs - 5, ys - 5), (xs + 5, ys + 5)], fill=BG, width=3)
                d.line([(xs - 5, ys + 5), (xs + 5, ys - 5)], fill=BG, width=3)
            elif estado == "objetivo":
                d.line([(xs - 5, ys), (xs - 1, ys + 5), (xs + 6, ys - 6)], fill=BG, width=3)
            else:
                d.ellipse([xs - 3, ys - 3, xs + 3, ys + 3], fill=BG)
        etiqueta_res = {"objetivo": "OBJETIVO", "stop": "STOP",
                        "por tiempo": "CERRADA POR TIEMPO"}.get(estado, str(estado).upper())
        mov = float(resultado.get("mov_pct") or 0)
        # banda superior con el veredicto, bien visible (sin emojis: la fuente no los tiene)
        d.rectangle([px_izq, y0, px_der, y0 + 46], fill=_blend(color_res, .14))
        _texto(d, (px_izq + 14, y0 + 11), f"{etiqueta_res}   {mov:+.2f}%", color_res, 26, True)
        _texto(d, (px_der - 14, y0 + 18),
               f"{int(resultado.get('velas') or 0)} {_velas(int(resultado.get('velas') or 0))} · "
               f"salida {float(resultado.get('cierre') or 0):,.4f}".rstrip("0").rstrip("."),
               TXT, 15, anchor="ra")
        y_min_etiquetas = y0 + 56          # las etiquetas de nivel arrancan DEBAJO de la banda
    # --- PLAQUETAS de los bloques (encima de las velas): el relleno de la banda ya se dibujó antes;
    # acá va solo la etiqueta, con el rango de precio y fondo oscuro para que se lea siempre
    for clave, color, etiqueta in (("ob_arriba", RED, "VENTA"), ("ob_abajo", GREEN, "COMPRA")):
        ob = sig.get(clave)
        g = banda_geom(ob) if ob else None
        if not g:
            continue
        z_lo, z_hi, yz0, yz1, xz = g
        txt = f"{etiqueta}  {z_lo:,.6g}–{z_hi:,.6g}  ·  {ob.get('estado')}"
        ancho_txt = d.textlength(txt, font=font(15, True))
        # la plaqueta se queda cerca del origen de la banda, pero si caería en la esquina de los
        # rótulos del RSI se corre al borde izquierdo del panel (así nunca se pisan)
        cx = min(max(xz + 10, px_izq + 10), px_der - ancho_txt - 16)
        if cx > px_der - 240:
            cx = px_izq + 10
        alto_banda = yz1 - yz0
        cy = yz0 - 30 if alto_banda < 30 else yz0 + alto_banda / 2 - 12
        cy = min(max(cy, y0 + 6), y1 - 32)
        d.rounded_rectangle([cx - 8, cy - 5, cx + ancho_txt + 8, cy + 23], radius=5,
                            fill=_blend(BG, .88), outline=_blend(color, .75), width=2)
        _texto(d, (cx, cy), txt, _blend(color, 1.0), 15, True)

    # --- eje de tiempo (abajo del panel del RSI): sin esto el gráfico no se puede comparar con
    # TradingView vela por vela, porque no se sabe qué hora es cada una
    n_lab = 7
    for k in range(n_lab):
        i = int(round(k * (n - 1) / (n_lab - 1)))
        t = dt.datetime.fromtimestamp(int(cerradas[i][0]) / 1000, dt.timezone.utc)
        etq = t.strftime("%d/%m %H:%M")
        xx = min(max(x(i), px_izq + 34), px_der - 34)
        _texto(d, (xx, rs1 + 20), etq, TXT_DIM, 13, anchor="mm")

    # --- pie con los números clave
    adm = sig.get("adx")
    pie = [f"cierre {closes[-1]:,.4f}".rstrip("0").rstrip(".") + f" · {fecha} UTC"]
    if adm is not None:
        pie.append(f"ADX {float(adm):.1f}")
    if sig.get("plus_di") is not None and sig.get("minus_di") is not None:
        pie.append(f"+DI {float(sig['plus_di']):.1f} / −DI {float(sig['minus_di']):.1f}")
    if sig.get("atr_pct"):
        pie.append(f"ATR {float(sig['atr_pct']):.2f}%")
    if sig.get("vol_ratio"):
        pie.append(f"volumen {float(sig['vol_ratio']):.2f}×")
    if sig.get("subida_pct"):
        pie.append(f"subida 30d {float(sig['subida_pct']):+.1f}%")
    if sig.get("caida_pct"):
        pie.append(f"caída 1h −{float(sig['caida_pct']):.1f}%")
    if sig.get("risk_pct"):
        pie.append(f"riesgo {float(sig['risk_pct']):.2f}%")
    # el R:R del pie tiene que ser el REAL (objetivo contra stop), no el nominal: con objetivo
    # estructural suele quedar por debajo de 1 y el gráfico no puede decir otra cosa que la alerta
    try:
        d_tp = abs(float(sig.get("tp") or 0) - entry)
        d_sl = abs(float(sig.get("sl") or 0) - entry)
        if d_tp and d_sl:
            pie.append(f"R:R 1:{d_tp / d_sl:.1f}")   # misma notación que la alerta (riesgo:premio)
        elif sig.get("rr"):
            pie.append(f"R:R {float(sig['rr']):.1f}")
    except Exception:
        pass
    pie.append(f"{n} velas · Binance spot · UTC")
    _texto(d, (M_IZQ + 6, H - 24), " · ".join(pie), TXT_DIM, 14)   # 14 px: la línea entera entra
    ancho_pie = d.textlength(" · ".join(pie), font=font(14))
    if ancho_pie > W - M_IZQ - 10:      # si igual no entra, se acorta la fuente
        _texto(d, (M_IZQ + 6, H - 24), " · ".join(pie), TXT_DIM, 12)

    img.save(out_path, "PNG", optimize=True)
    return out_path
