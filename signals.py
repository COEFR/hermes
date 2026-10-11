#!/usr/bin/env python3
"""
Bot de ALERTAS de divergencia de RSI + ADX + volumen (Binance spot).

Es un bot de alertas: NO opera, NO pide claves de exchange, NO toca fondos. Solo avisa cuando
un activo cumple las condiciones y te muestra los indicadores para que decidas vos.

Condiciones por defecto (medidas en shorts_adx.py sobre 1 año y 15 pares; detalle en NOTAS.md):
  - Señal: divergencia BAJISTA de RSI(14) entre los dos últimos pivotes de swing
    (0.5% de diferencia de precio y 2 puntos de RSI como mínimos).
  - Volumen: la vela de entrada trae >= 1.2x la media de 20 velas. El volumen es el filtro que
    sí aporta: en 4h los shorts pasan de -0.53% a +2.0% de movimiento medio por alerta.
  - ADX(14) >= 25 (--min-adx). Ojo: el ADX SOLO empeora el resultado (-0.18% en 15m), pero
    combinado con el volumen es la mejor combinación medida (4h: 58% al objetivo, +4.3% por
    alerta). Se puede apagar con --min-adx 0.
  - Temporalidades por defecto: 15m, 1h, 4h, 1d (--intervals). En 15m conviene activar
    --min-atr-pct 1.5: medido, el 15m sin ese filtro queda en -0.02% de movimiento por alerta
    (la comisión pesa mucho frente a un stop de 0.9%); con ATR >= 1.5% pasa a +1.84%.
  - Lado: solo SHORT por defecto (--side short|long|both).
  - Dos niveles de alerta (--min-adx = umbral del nivel premium, por defecto 25):
      ⭐ PREMIUM: divergencia + volumen >= 1.2x + ADX >= 25  → tendencia confirmada (pocas).
      📡 NORMAL:  divergencia + volumen, pero ADX < 25       → sin tendencia definida (más).
    Cada alerta dice de qué nivel es y trae el histórico medido de ESE nivel (shorts_adx.py).
    Con --normal-sin-volumen el nivel normal tampoco exige volumen (muchas más alertas).
  - Cooldown de 4 h por par + deduplicación: no repite la misma alerta.
  - La alerta lleva: RSI de los dos pivotes, ADX con +DI/−DI, volumen relativo, OBV de 20 velas,
    entrada/stop/objetivo en ATR, % de riesgo y el histórico medido de esa temporalidad.

Costo: cero tokens de LLM. El script calcula y manda el mensaje él mismo por la Bot API.
"""
import argparse
import json
import os
import time
import urllib.parse
import urllib.request
import uuid

import scoring as SC
import orderblocks as OB

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(HERE, "state.json")
LOG_DIR = os.path.join(HERE, "logs")
SHORTS_RESULTS = os.path.join(HERE, "shorts_results.json")
BASE = "https://api.binance.com"
# Hosts de datos, en orden. Medido: `api.binance.com` devuelve **HTTP 451** a IPs de EE.UU. (los
# runners de GitHub están ahí) y `data-api.binance.vision` (el endpoint público de datos de Binance)
# responde 200 con las MISMAS velas. Se usa el primero que conteste, una vez por corrida.
BASE_HOSTS = ["https://api.binance.com", "https://data-api.binance.vision"]
_BASE_OK = None
MS = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}

WATCHLIST_N = 45          # pares vigilados (top por volumen). Estaba en 20 y las señales bajaron a
                          # 2-4 por día; se subió a 45 (no 60) porque el ciclo completo tiene que entrar
                          # en los 15 min del reloj: con 60 los flujos tardaban 630 s y algún ciclo se
                          # pasaba de tiempo (se vio un completed/failure). Con 45 quedan ~8 min.
WATCHLIST_TTL = 6 * 3600
PIVOT_K = 3
PIVOT_LOOKBACK = 60
FRESH_BARS = 2
MIN_PRICE_DIFF = 0.005
MIN_RSI_DIFF = 2.0
VOL_MULT = 1.2
USE_VOL = True                     # --no-vol lo apaga
MIN_ADX = 25.0                     # --min-adx 0 lo apaga
SIDE = "short"                     # --side long|both
INTERVALS = ["15m", "1h", "4h", "1d"]   # --intervals
MIN_ATR_PCT = 0.0                  # --min-atr-pct 1.5 filtra stops demasiado ajustados
NORMAL_SIN_VOLUMEN = False         # --normal-sin-volumen: el nivel normal no exige volumen
NORMAL_INTERVALS = None            # --normal-intervals: limitar el nivel normal a ciertas TF (None = todas)
TP_MULT, SL_MULT = 2.0, 1.5         # stop 1.5xATR: medido como el menos malo de 10 estilos
# Niveles de REFERENCIA de los flujos largos, medidos en `niveles_long.py` (20 pares, 3 años):
#   momentum: stop 1.5×ATR / objetivo 3×ATR (n=811 → +0.84% por señal, 1ª ventana −1.23%)
#   rebote:   stop 1.5×ATR / objetivo 6×ATR, tope 40 velas (n=379 → +0.15%, 2ª ventana −0.10%)
# Ninguna estructura quedó positiva en las 3 ventanas: son referencia para poder seguir la señal.
MOM_SL, MOM_TP = 1.5, 3.0
REB_SL, REB_TP = 1.5, 6.0
REF_NIVELES = {"momentum": (MOM_SL, MOM_TP, 20, 811, 0.84, -1.23),
               "rebote": (REB_SL, REB_TP, 40, 379, 0.15, -0.10)}
TARGET_MODE = "soporte"            # --target: objetivo en el soporte real (mejor medido) o 2xATR
TP_MIN_ATR, TP_MAX_ATR = 0.8, 5.0   # límites MEDIDOS del objetivo estructural (no inventar otros)
COOLDOWN_H = 2        # horas de enfriamiento por par (era 4). Con el mercado alcista las divergencias
                      # bajistas son escasas: 2 h deja repetir un setup bueno sin spamear la misma señal.
MAX_ALERTS = 12
SEEN_TTL = 7 * 86_400_000
EXCLUDE = ("USDC", "FDUSD", "TUSD", "BUSD", "DAI", "USDP", "EUR", "USDTB", "AEUR", "USD1")
LEV_SUFFIX = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")
STATS_KEY = "+ volumen ≥1.2× y ADX ≥ 25"
CALIB = SC.load_calib()            # calibración del puntaje 1-10 (score_study.py)
MIN_SCORE = 0                      # --min-score: 0 = manda todas (volumen), 7+ = solo las fuertes
TIPO = "divergencia"               # --tipo: divergencia | momentum | rebote
# SEPARAR POR CALIDAD EN SUB-CANALES (pedido de Dylan): como las señales de cantidad superan a las
# buenas, cada señal se manda al canal que le corresponde por su puntaje 1-10:
#   puntaje ≥8 → canal de calidad · 5-7 → canal medio · <5 → canal de cantidad (el del volumen).
# Se configura con --canal-alto / --canal-medio / --canal-bajo (los ids los pasa gh_run.py); si no se
# pasa ninguno, todo sigue yendo al canal de siempre.
CANAL_ALTO = ""
CANAL_MEDIO = ""
CANAL_BAJO = ""
CALIDAD_ALTA = 8
CALIDAD_MEDIA = 5


def canal_por_puntaje(score):
    """El canal que le toca a esta señal según su puntaje (vacío = usar el canal por defecto)."""
    s = score if isinstance(score, (int, float)) else 0
    if s >= CALIDAD_ALTA:
        return CANAL_ALTO
    if s >= CALIDAD_MEDIA:
        return CANAL_MEDIO
    return CANAL_BAJO


DISCORD_OVERRIDE = None            # --discord-channel: mandar a otro canal (p.ej. el radar)
STREAM_LABEL = ""                  # --label: etiqueta del flujo en el encabezado (p.ej. "RADAR")
SEND_TELEGRAM = True               # --no-telegram: flujos de volumen alto que solo van a Discord
INDIVIDUAL = False                 # --individual: una señal = un mensaje (no agrupadas)
CHART = True                       # --no-chart: alertas sin gráfico adjunto
CHART_DIR = os.path.join(HERE, "charts")
CHART_KEEP = 400                   # PNGs que se conservan (los más viejos se borran solos)
LOG_SENALES = os.path.join(HERE, "signals_log.jsonl")   # señales enviadas: las sigue `tracker.py`
WATCHLIST_EXTRA = os.path.join(HERE, "watchlist_extra.json")   # pares que pide el usuario
_DERIV_CACHE = {}                  # funding/OI por par dentro de una corrida
FEAT_LABEL = {"adx": "ADX", "di": "−DI vs +DI", "vol": "volumen", "obv": "OBV",
              "atr": "ATR%", "rsi": "RSI", "rsi_gap": "separación del RSI",
              "px_gap": "salto de precio", "piv_gap": "velas entre pivotes"}


# ---------------------------------------------------------------- infra
def log(msg):
    os.makedirs(LOG_DIR, exist_ok=True)
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line)
    p = os.path.join(LOG_DIR, "signals.log")
    try:
        if os.path.exists(p) and os.path.getsize(p) > 2_000_000:   # rotacion simple
            os.replace(p, p + ".1")
    except Exception:
        pass
    with open(p, "a") as f:
        f.write(line + "\n")


def http_json(url, tries=4):
    for a in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "hermes-alerts/2.0"})
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read().decode())
        except Exception:
            if a == tries - 1:
                raise
            time.sleep(1.5 * (a + 1))
    raise RuntimeError("http fallo: " + url)


def env_file(path="~/.hermes/.env"):
    out = {}
    p = os.path.expanduser(path)
    if os.path.exists(p):
        for line in open(p):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _multipart(fields, files):
    """Cuerpo multipart/form-data con stdlib (urllib no lo arma solo). `files` = {campo: (nombre, bytes, tipo)}."""
    borde = "----hermes" + uuid.uuid4().hex
    trozos = []
    for k, v in fields.items():
        trozos.append(f"--{borde}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    for k, (nombre, datos, tipo) in files.items():
        trozos.append(f"--{borde}\r\nContent-Disposition: form-data; name=\"{k}\"; "
                      f"filename=\"{nombre}\"\r\nContent-Type: {tipo}\r\n\r\n".encode()
                      + datos + b"\r\n")
    trozos.append(f"--{borde}--\r\n".encode())
    return b"".join(trozos), f"multipart/form-data; boundary={borde}"


def _leer(path):
    with open(path, "rb") as fh:
        return fh.read()


def send_telegram(text, image=None):
    env = env_file()
    token = env.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = env.get("TELEGRAM_HOME_CHANNEL") or os.environ.get("TELEGRAM_HOME_CHANNEL")
    if not token or not chat:
        log("!! sin TELEGRAM_BOT_TOKEN / TELEGRAM_HOME_CHANNEL: no se puede enviar")
        print(text)
        return False
    try:
        if image and os.path.exists(image):
            # Telegram corta los pies de foto en 1024: la imagen lleva el resumen y el texto largo
            # va aparte, para no perder ninguna cifra.
            resumen = text if len(text) <= 1000 else text[:900] + "…"
            cuerpo, ctype = _multipart({"chat_id": chat, "parse_mode": "HTML", "caption": resumen},
                                       {"photo": (os.path.basename(image), _leer(image), "image/png")})
            url, accion = f"https://api.telegram.org/bot{token}/sendPhoto", "sendPhoto"
        else:
            cuerpo = urllib.parse.urlencode({
                "chat_id": chat, "text": text[:4090],
                "parse_mode": "HTML", "disable_web_page_preview": "true"}).encode()
            ctype = "application/x-www-form-urlencoded"
            url, accion = f"https://api.telegram.org/bot{token}/sendMessage", "sendMessage"
        with urllib.request.urlopen(
                urllib.request.Request(url, data=cuerpo, headers={"Content-Type": ctype}),
                timeout=40) as r:
            ok = json.loads(r.read().decode()).get("ok", False)
        log(f"telegram {accion} enviado ok={ok}")
        if image and os.path.exists(image) and len(text) > 1000:
            send_telegram(text, None)          # el detalle completo, como mensaje aparte
        return ok
    except Exception as e:
        log(f"!! fallo telegram: {e}")
        return False


def to_discord(text):
    """Traduce el HTML de Telegram al markdown de Discord."""
    out = text
    for a, b in (("<b>", "**"), ("</b>", "**"), ("<i>", "*"), ("</i>", "*"),
                 ("<code>", "`"), ("</code>", "`"), ("<s>", "~~"), ("</s>", "~~")):
        out = out.replace(a, b)
    return out.replace("<br>", "\n")


def split_discord(text, limit=1900):
    """Discord corta a 2000 caracteres: parte por bloques (párrafos) sin cortar uno al medio."""
    partes, cur = [], ""
    for bloque in text.split("\n\n"):
        if cur and len(cur) + len(bloque) + 2 > limit:
            partes.append(cur)
            cur = bloque
        else:
            cur = f"{cur}\n\n{bloque}" if cur else bloque
    if cur:
        partes.append(cur)
    return partes


def send_discord(text, env=None, image=None, canal=None):
    """Manda a Discord. Usa webhook si hay DISCORD_WEBHOOK_URL; si no, bot + canal.
    Con `image` la primera parte va como adjunto (multipart), el resto como mensajes normales.
    `canal` permite mandar ESA señal a un canal distinto (se usa para separar por calidad)."""
    env = env or env_file()
    hook = env.get("DISCORD_WEBHOOK_URL") or os.environ.get("DISCORD_WEBHOOK_URL")
    token = env.get("DISCORD_BOT_TOKEN") or os.environ.get("DISCORD_BOT_TOKEN")
    canal = (canal or DISCORD_OVERRIDE or env.get("DISCORD_SIGNALS_CHANNEL")
             or env.get("DISCORD_HOME_CHANNEL") or os.environ.get("DISCORD_SIGNALS_CHANNEL")
             or os.environ.get("DISCORD_HOME_CHANNEL"))
    if not hook and not (token and canal):
        return None                       # Discord no configurado: no es un error
    partes = split_discord(to_discord(text))
    ok_all = True
    for i, parte in enumerate(partes, 1):
        url = hook or f"https://discord.com/api/v10/channels/{canal}/messages"
        headers = {"User-Agent": "hermes-alerts/3.1"}
        if not hook:
            headers["Authorization"] = f"Bot {token}"
        adjuntar = bool(image and i == 1 and os.path.exists(image))
        if adjuntar:
            payload = {"content": parte, **({"username": "Señales crypto"} if hook else {})}
            cuerpo, ctype = _multipart({"payload_json": json.dumps(payload)},
                                       {"files[0]": (os.path.basename(image), _leer(image), "image/png")})
            headers["Content-Type"] = ctype
        else:
            cuerpo = json.dumps({"content": parte,
                                 **({"username": "Señales crypto"} if hook else {})}).encode()
            headers["Content-Type"] = "application/json"
        try:
            req = urllib.request.Request(url, data=cuerpo, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=40) as r:
                code = r.status
            if code not in (200, 204):
                ok_all = False
                log(f"!! discord devolvió {code} en la parte {i}/{len(partes)}")
        except Exception as e:
            ok_all = False
            log(f"!! fallo discord (parte {i}/{len(partes)}): {e}")
    log(f"discord enviado ok={ok_all} ({len(partes)} mensaje(s){', con imagen' if image else ''})")
    return ok_all


def deliver(text, image=None, canal=None):
    """Manda por todos los canales configurados (Telegram y/o Discord). Devuelve qué salió bien."""
    env = env_file()
    res = {}
    if not SEND_TELEGRAM:
        res["telegram"] = None
    elif (env.get("TELEGRAM_BOT_TOKEN") and env.get("TELEGRAM_HOME_CHANNEL")):
        res["telegram"] = send_telegram(text, image)
    else:
        res["telegram"] = None
    res["discord"] = send_discord(text, env, image, canal=canal)
    if not any(v for v in res.values()):
        log("!! ningún canal pudo enviar; imprimo el contenido")
        print(text)
    return res


def load_state():
    if os.path.exists(STATE_PATH):
        try:
            return json.load(open(STATE_PATH))
        except Exception:
            pass
    return {"watchlist": {"ts": 0, "syms": []}, "seen": {}, "cooldown": {}}


def save_state(st):
    now = int(time.time() * 1000)
    st["seen"] = {k: v for k, v in st["seen"].items() if now - v < SEEN_TTL}
    json.dump(st, open(STATE_PATH, "w"), indent=1)


# ---------------------------------------------------------------- datos
def base_datos():
    """Host de datos en uso: prueba los candidatos (una vez por corrida) y recuerda el que responda."""
    global _BASE_OK
    if _BASE_OK:
        return _BASE_OK
    for host in BASE_HOSTS:
        try:
            http_json(f"{host}/api/v3/ping", tries=1)
            _BASE_OK = host
            return host
        except Exception:
            continue
    _BASE_OK = BASE_HOSTS[0]      # ninguno respondió: se deja el principal (el error se verá en el log)
    return _BASE_OK


def klines(symbol, interval, limit=300):
    return http_json(f"{base_datos()}/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}")


FUT = "https://fapi.binance.com"


def derivados(symbol):
    """Funding y variación del interés abierto (24 h) del perpetuo: contexto para decidir un short.
    Nunca rompe la alerta: si la API de futuros falla, devuelve lo poco que tenga."""
    out = {}
    try:
        f = http_json(f"{FUT}/fapi/v1/premiumIndex?symbol={symbol}", tries=2)
        out["funding"] = float(f.get("lastFundingRate") or 0) * 100      # % por 8 h
    except Exception:
        pass
    try:
        h = http_json(f"{FUT}/futures/data/openInterestHist?symbol={symbol}&period=1h&limit=25", tries=2)
        if isinstance(h, list) and len(h) >= 2:
            a = float(h[0].get("sumOpenInterestValue") or 0)
            b = float(h[-1].get("sumOpenInterestValue") or 0)
            if a > 0:
                out["oi_pct"] = (b / a - 1) * 100                        # % en 24 h
    except Exception:
        pass
    return out


def linea_derivados(d):
    """Texto de contexto de derivados (vacío si no hay datos)."""
    if not d:
        return ""
    partes = []
    if "funding" in d:
        f = d["funding"]
        quien = "longs pagan a shorts" if f > 0 else "shorts pagan a longs"
        partes.append(f"💸 Funding {f:+.4f}%/8h ({quien})")
    if "oi_pct" in d:
        partes.append(f"📦 Interés abierto 24h {d['oi_pct']:+.1f}%")
    return " · ".join(partes)


def top_symbols(n):
    d = http_json(f"{base_datos()}/api/v3/ticker/24hr")
    rows = []
    for x in d:
        s = x["symbol"]
        if not s.endswith("USDT") or s.endswith(LEV_SUFFIX) or s[:-4] in EXCLUDE:
            continue
        rows.append((s, float(x["quoteVolume"])))
    rows.sort(key=lambda r: -r[1])
    return [r[0] for r in rows[:n]]


def watchlist_extra():
    """Pares que el usuario agregó a mano (`watchlist.py add SYMBOL`). Se respetan siempre."""
    try:
        return [s.strip().upper() for s in json.load(open(WATCHLIST_EXTRA)) if s.strip()]
    except Exception:
        return []


def watchlist(st):
    now = int(time.time() * 1000)
    w = st["watchlist"]
    extras = watchlist_extra()
    if (w["syms"] and now - w["ts"] < WATCHLIST_TTL * 1000
            and w.get("top", WATCHLIST_N) == WATCHLIST_N):
        return list(dict.fromkeys(w["syms"] + extras))
    syms = top_symbols(WATCHLIST_N)
    st["watchlist"] = {"ts": now, "syms": syms, "top": WATCHLIST_N}
    log(f"watchlist actualizada: {len(syms)} pares (top {WATCHLIST_N})"
        + (f" + {len(extras)} propios: {', '.join(extras)}" if extras else ""))
    return list(dict.fromkeys(syms + extras))


# ---------------------------------------------------------------- indicadores
def rsi_wilder(closes, period=14):
    n, out = len(closes), [None] * len(closes)
    if n <= period:
        return out
    g = l = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        g += max(d, 0.0)
        l += max(-d, 0.0)
    ag, al = g / period, l / period
    for i in range(period, n):
        if i > period:
            d = closes[i] - closes[i - 1]
            ag = (ag * (period - 1) + max(d, 0.0)) / period
            al = (al * (period - 1) + max(-d, 0.0)) / period
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def atr_wilder(candles, period=14):
    n, out = len(candles), [None] * len(candles)
    if n <= period + 1:
        return out
    trs = []
    for i in range(1, n):
        h, l, pc = float(candles[i][2]), float(candles[i][3]), float(candles[i - 1][4])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    a = sum(trs[:period]) / period
    out[period] = a
    for i in range(period + 1, n):
        a = (a * (period - 1) + trs[i - 1]) / period
        out[i] = a
    return out


def adx_wilder(candles, period=14):
    """ADX de Wilder con +DI/-DI. [(adx, plus_di, minus_di), ...]"""
    n = len(candles)
    out = [None] * n
    if n < 2 * period + 2:
        return out
    tr = [0.0] * n
    pdm = [0.0] * n
    mdm = [0.0] * n
    for i in range(1, n):
        h, l, pc = float(candles[i][2]), float(candles[i][3]), float(candles[i - 1][4])
        tr[i] = max(h - l, abs(h - pc), abs(l - pc))
        up = float(candles[i][2]) - float(candles[i - 1][2])
        dn = float(candles[i - 1][3]) - float(candles[i][3])
        pdm[i] = up if (up > dn and up > 0) else 0.0
        mdm[i] = dn if (dn > up and dn > 0) else 0.0
    atr_s = sum(tr[1:period + 1])
    pdm_s = sum(pdm[1:period + 1])
    mdm_s = sum(mdm[1:period + 1])
    adx = None
    dxs = []
    for i in range(period, n):
        if i > period:
            atr_s = atr_s - atr_s / period + tr[i]
            pdm_s = pdm_s - pdm_s / period + pdm[i]
            mdm_s = mdm_s - mdm_s / period + mdm[i]
        if atr_s <= 0:
            continue
        pdi = 100 * pdm_s / atr_s
        mdi = 100 * mdm_s / atr_s
        denom = pdi + mdi
        dx = 100 * abs(pdi - mdi) / denom if denom > 0 else 0.0
        dxs.append(dx)
        if adx is None:
            if len(dxs) >= period:
                adx = sum(dxs[-period:]) / period
            else:
                continue
        else:
            adx = (adx * (period - 1) + dx) / period
        out[i] = (adx, pdi, mdi)
    return out


def obv_slope(candles, bars=20):
    """OBV de las últimas `bars` velas normalizado por volumen. <0 = distribución."""
    n = len(candles)
    out = [None] * n
    if n < bars + 2:
        return out
    diffs = [0.0] * n
    for i in range(1, n):
        c0, c1 = float(candles[i - 1][4]), float(candles[i][4])
        v = float(candles[i][5])
        diffs[i] = v if c1 > c0 else (-v if c1 < c0 else 0.0)
    run = sum(diffs[1:bars + 1])
    vsum = sum(float(candles[i][5]) for i in range(1, bars + 1))
    for i in range(bars, n):
        if i > bars:
            run += diffs[i] - diffs[i - bars]
            vsum += float(candles[i][5]) - float(candles[i - bars][5])
        out[i] = run / vsum if vsum > 0 else None
    return out


def sma(vals, period):
    n, out = len(vals), [None] * len(vals)
    s = 0.0
    for i, v in enumerate(vals):
        s += v
        if i >= period:
            s -= vals[i - period]
        if i >= period - 1:
            out[i] = s / period
    return out


def pivots(candles, k=PIVOT_K):
    lo, hi = [], []
    for i in range(k, len(candles) - k):
        lows = [float(candles[j][3]) for j in range(i - k, i + k + 1)]
        highs = [float(candles[j][2]) for j in range(i - k, i + k + 1)]
        if float(candles[i][3]) == min(lows):
            lo.append(i)
        if float(candles[i][2]) == max(highs):
            hi.append(i)
    return lo, hi


def niveles(candles, price, k=PIVOT_K, lookback=250):
    """Soportes y resistencias reales: pivotes por debajo y por encima del precio de entrada.
    Devuelve (soportes, resistencias) con hasta 2 niveles cada uno, del más cercano al más lejano."""
    serie = candles[:-1]
    serie = serie[-lookback:] if len(serie) > lookback else serie
    lo_p, hi_p = pivots(serie, k)
    bajos = sorted({float(serie[i][3]) for i in lo_p if float(serie[i][3]) < price}, reverse=True)
    altos = sorted({float(serie[i][2]) for i in hi_p if float(serie[i][2]) > price})
    return bajos[:2], altos[:2]


def measured_stats():
    """Histórico medido por temporalidad y NIVEL (shorts_adx.py → shorts_results.json)."""
    try:
        r = json.load(open(SHORTS_RESULTS))
        tiers = r.get("tiers") or {}
        out = {tf: d for tf, d in tiers.items()
               if any((d.get(k) or {}).get("n", 0) >= 5 for k in ("premium", "normal"))}
        if out:
            return out
        return {tf: {"premium": (combos.get(STATS_KEY) or {})}
                for tf, combos in r.get("por_tf", {}).items()
                if (combos.get(STATS_KEY) or {}).get("n", 0) >= 10}
    except Exception:
        return {}


# ---------------------------------------------------------------- detección
def find_signals(symbol, tf, candles):
    closed = candles[:-1]                    # solo velas cerradas
    if len(closed) < 60:
        return []
    closes = [float(c[4]) for c in closed]
    highs = [float(c[2]) for c in closed]
    lows = [float(c[3]) for c in closed]
    vols = [float(c[5]) for c in closed]
    rsi = rsi_wilder(closes)
    atr = atr_wilder(closed)
    adx = adx_wilder(closed)
    obv = obv_slope(closed)
    v_sma = sma(vols, 20)
    lo_p, hi_p = pivots(closed)
    bloques = OB.detectar(closed, atr)      # bloques de orden (oferta/demanda) para el contexto
    out = []
    last = len(closed) - 1

    search = []
    if SIDE in ("short", "both"):
        search.append((hi_p, "short"))
    if SIDE in ("long", "both"):
        search.append((lo_p, "long"))

    for piv, direction in search:
        for i in range(1, len(piv)):
            p1, p2 = piv[i - 1], piv[i]
            if p2 - p1 > PIVOT_LOOKBACK or p2 - p1 < PIVOT_K * 2:
                continue
            e = p2 + PIVOT_K
            if e < last - (FRESH_BARS - 1) or e > last:
                continue                     # solo alertas frescas, ya confirmadas
            if rsi[p1] is None or rsi[p2] is None or atr[e] is None or v_sma[e] is None:
                continue
            if direction == "short":
                v1, v2 = highs[p1], highs[p2]
                if not (v2 > v1 and (v2 - v1) / v1 >= MIN_PRICE_DIFF):
                    continue
                if not (rsi[p2] < rsi[p1] - MIN_RSI_DIFF):
                    continue
            else:
                v1, v2 = lows[p1], lows[p2]
                if not (v2 < v1 and (v1 - v2) / v1 >= MIN_PRICE_DIFF):
                    continue
                if not (rsi[p2] > rsi[p1] + MIN_RSI_DIFF):
                    continue
            vol_ratio = vols[e] / v_sma[e] if v_sma[e] else 0
            a = adx[e] if e < len(adx) else None
            vol_ok = vol_ratio >= VOL_MULT
            premium = vol_ok and a is not None and a[0] >= MIN_ADX
            if not premium and USE_VOL and not NORMAL_SIN_VOLUMEN and not vol_ok:
                continue                     # sin volumen no hay alerta (ni del nivel normal)
            tier = "premium" if premium else "normal"
            if tier == "normal" and NORMAL_INTERVALS and tf not in NORMAL_INTERVALS:
                continue
            tipo = "Clásica bajista" if direction == "short" else "Clásica alcista"
            entry = closes[e]
            risk = SL_MULT * atr[e]
            if entry <= 0 or risk <= 0:
                continue
            if MIN_ATR_PCT > 0 and 100 * atr[e] / entry < MIN_ATR_PCT:
                continue
            soportes, resistencias = niveles(closed, entry)
            ob_arriba, ob_abajo = OB.relevante(bloques, entry)
            sl = entry - risk if direction == "long" else entry + risk
            if TARGET_MODE == "soporte" and direction == "short" and soportes:
                dist = max(TP_MIN_ATR, min((entry - soportes[0]) / atr[e], TP_MAX_ATR))
                tp = entry - dist * atr[e]
                tp_pct = 100 * (entry - tp) / entry
            else:
                tp = entry + TP_MULT * atr[e] if direction == "long" else entry - TP_MULT * atr[e]
                tp_pct = 100 * risk / entry
            out.append({
                "sym": symbol, "tf": tf, "dir": direction,
                "pivot_ts": int(closed[p2][0]), "entry_ts": int(closed[e][0]),
                "entry": entry, "sl": sl, "tp": tp,
                "risk_pct": 100 * risk / entry, "atr_pct": 100 * atr[e] / entry,
                "rr": TP_MULT / SL_MULT,
                "px_prev": v1, "px_now": v2,
                "rsi_prev": rsi[p1], "rsi_now": rsi[p2],
                "vol_ratio": vol_ratio,
                "adx": a[0] if a else None,
                "plus_di": a[1] if a else None,
                "minus_di": a[2] if a else None,
                "obv": obv[e] if e < len(obv) else None,
                "tier": tier, "vol_ok": vol_ok,
                "pivot_gap": p2 - p1,
                "tipo": tipo, "soportes": soportes, "resistencias": resistencias,
                "tp_pct": tp_pct,
                # bloques de orden: zonas de oferta (arriba) y demanda (abajo)
                "ob_arriba": ({k: ob_arriba[k] for k in ("lo", "hi", "estado", "fuerza", "ts")}
                              if ob_arriba else None),
                "ob_abajo": ({k: ob_abajo[k] for k in ("lo", "hi", "estado", "fuerza", "ts")}
                             if ob_abajo else None),
                "_bloques": bloques,
            })
            sig = out[-1]
            cal = SC.score_1_10(SC.features(sig), CALIB)
            if cal:
                sig["score"] = cal["score"]
                sig["score_label"] = cal["etiqueta"]
                sig["score_why"] = SC.explain(SC.features(sig), CALIB)[:3]
                sig["score_stats"] = SC.bucket_stats(CALIB, cal["score"])
    return out


def load_long_calib():
    try:
        with open(os.path.join(HERE, "long_calib.json")) as fh:
            return json.load(fh)
    except Exception:
        return None


LONG_CALIB = load_long_calib()


def puntaje_long(valor, seccion):
    """Decil medido del valor dentro de la distribución histórica de ese tipo de señal."""
    if not LONG_CALIB or seccion not in LONG_CALIB:
        return None
    datos = LONG_CALIB[seccion]
    cortes = datos.get("decile_cuts") or []
    s = 1
    for c in cortes:
        if valor >= c:
            s += 1
    s = max(1, min(10, s))
    for d in datos.get("deciles", []):
        if d["score"] == s:
            return {"score": s, **d}
    return None


def find_momentum(symbol, tf, candles):
    """Nuevo MÁXIMO de 30 días en velas diarias (señal de compra / momentum).

    Niveles de referencia MEDIDOS (`niveles_long.py`, 20 pares, 3 años, n=811): stop 1.5×ATR /
    objetivo 3×ATR con tope de 20 velas → +0.84% por señal, pero negativo en la primera de las 3
    ventanas (−1.23%). Otras estructuras medidas fueron peores o menos estables. Se publican como
    referencia para poder SEGUIR la señal (y que el tracker la evalúe), no como ventaja probada.
    """
    if tf != "1d":
        return []
    cerradas = candles[:-1]
    L = 30
    if len(cerradas) < L + 5:
        return []
    closes = [float(c[4]) for c in cerradas]
    atr = atr_wilder(cerradas)
    n = len(closes)
    out = []
    desde = max(L + 1, n - FRESH_BARS)
    for i in range(desde, n):
        if closes[i] != max(closes[i - L:i + 1]):
            continue
        subida = 100 * (closes[i] / closes[i - L] - 1)
        cal = puntaje_long(subida, "momentum")
        entry = closes[i]
        a = atr[i] if i < len(atr) else None
        sl = entry - MOM_SL * a if a else 0.0
        tp = entry + MOM_TP * a if a else 0.0
        out.append({"sym": symbol, "tf": tf, "dir": "long",
                    "tipo": "Momentum (nuevo máximo de 30 días)",
                    "pivot_ts": int(cerradas[i][0]), "entry_ts": int(cerradas[i][0]),
                    "entry": entry, "subida_pct": subida,
                    "max_previo": max(closes[i - L:i]),
                    "score": (cal or {}).get("score"), "score_label": "momentum",
                    "score_stats": cal, "horizonte": "20 velas (días)",
                    "tier": "long", "vol_ok": True,
                    "atr_pct": 100 * a / entry if a else 0.0,
                    "risk_pct": 100 * MOM_SL * a / entry if a else 0.0,
                    "sl": sl, "tp": tp, "niveles_ref": True,
                    "soportes": [], "resistencias": [], "adx": None, "plus_di": None,
                    "minus_di": None, "obv": None, "vol_ratio": 0.0,
                    "rsi_now": 50.0, "rsi_prev": 50.0,
                    "px_prev": closes[i - L], "px_now": closes[i],
                    "tp_pct": 100 * MOM_TP * a / entry if a else 0.0, "pivot_gap": L})
    return out


def find_rebote(symbol, tf, candles):
    """Caída violenta en 1 hora con volumen alto (señal de compra / rebote).

    Niveles de referencia MEDIDOS (`niveles_long.py`, 20 pares, 3 años, n=379): stop 1.5×ATR /
    objetivo 6×ATR con tope de 40 velas → +0.15% por señal, con la 2ª ventana en −0.10%. Es la
    estructura menos mala de las 6 medidas; se publica como referencia para poder seguirla.
    """
    if tf != "1h":
        return []
    cerradas = candles[:-1]
    if len(cerradas) < 30:
        return []
    closes = [float(c[4]) for c in cerradas]
    vols = [float(c[5]) for c in cerradas]
    atr = atr_wilder(cerradas)
    n = len(closes)
    out = []
    for i in range(max(21, n - FRESH_BARS), n):
        caida = 1 - closes[i] / closes[i - 1]
        vmed = sum(vols[i - 20:i]) / 20
        if not vmed or caida < 0.05 or vols[i] < 3 * vmed:
            continue
        cal = puntaje_long(caida * 100, "rebote")
        entry = closes[i]
        a = atr[i] if i < len(atr) else None
        out.append({"sym": symbol, "tf": tf, "dir": "long",
                    "tipo": "Rebote tras caída violenta",
                    "pivot_ts": int(cerradas[i][0]), "entry_ts": int(cerradas[i][0]),
                    "entry": entry, "caida_pct": caida * 100, "vol_ratio": vols[i] / vmed,
                    "score": (cal or {}).get("score"), "score_label": "rebote",
                    "score_stats": cal, "horizonte": "40 velas (horas)",
                    "tier": "long", "vol_ok": True,
                    "atr_pct": 100 * a / entry if a else 0.0,
                    "risk_pct": 100 * REB_SL * a / entry if a else 0.0,
                    "sl": entry - REB_SL * a if a else 0.0,
                    "tp": entry + REB_TP * a if a else 0.0, "niveles_ref": True,
                    "soportes": [], "resistencias": [], "adx": None, "plus_di": None,
                    "minus_di": None, "obv": None, "rsi_now": 50.0, "rsi_prev": 50.0,
                    "px_prev": closes[i - 1], "px_now": entry,
                    "tp_pct": 100 * REB_TP * a / entry if a else 0.0, "pivot_gap": 1})
    return out


# ---------------------------------------------------------------- formato
def fmt_momentum(s, stats=None):
    """Formato de la señal de momentum (nuevo máximo de 30 días)."""
    sep = "━" * 22
    l = ["🟢 NUEVO MÁXIMO DE 30 DÍAS", sep,
         f"📡 Par: {s['sym']}",
         f"⏱ Timeframe: {s['tf']}",
         f"💰 Precio: ${fmt_precio(s['entry'])}",
         f"📈 Subida de 30 días: {s['subida_pct']:+.1f}%",
         f"🎯 Máximo previo de 30d: ${fmt_precio(s['max_previo'])} (roto)"]
    st = s.get("score_stats")
    if st:
        l.append(f"⚡ Score: {st['score']}/10 (por magnitud del momentum)")
        l.append(f"📊 Histórico medido: señales en este decil → {st['retorno_pct']:+.2f}% de media a "
                 f"7 días · mediana {st['mediana_pct']:+.2f}% · aciertos {st['aciertos']}% (n={st['n']})")
    l += [f"🎯 Niveles de REFERENCIA (medidos, 3 años, n={REF_NIVELES['momentum'][3]}): "
          f"stop ${fmt_precio(s['sl'])} (−{s.get('risk_pct', 0):.2f}%) · "
          f"objetivo ${fmt_precio(s['tp'])} (+{s.get('tp_pct', 0):.2f}%) · "
          f"stop {MOM_SL:g}×ATR / objetivo {MOM_TP:g}×ATR, tope {REF_NIVELES['momentum'][2]} velas",
          f"🧭 Horizonte: {s.get('horizonte', '20 velas (días)')}",
          f"🕐 {time.strftime('%Y-%m-%d %H:%M')} (UTC−4)",
          "⚠️ Es una señal de COMPRA. Medido: +0.84% por señal en 3 años (n=811), pero la 1ª de las 3 "
          "ventanas cerró en −1.23% → los niveles son una **referencia para poder seguirla**, no una "
          "ventaja probada; el riesgo real es la volatilidad del activo.", sep]
    return "\n".join(l)


def fmt_rebote(s, stats=None):
    """Formato de la señal de rebote tras caída violenta."""
    sep = "━" * 22
    l = ["🟢 REBOTE TRAS CAÍDA VIOLENTA", sep,
         f"📡 Par: {s['sym']}",
         f"⏱ Timeframe: {s['tf']}",
         f"💰 Precio de entrada: ${fmt_precio(s['entry'])}",
         f"📉 Caída: −{s['caida_pct']:.2f}% en 1 vela",
         f"📊 Volumen: {s['vol_ratio']:.1f}× la media de 20"]
    st = s.get("score_stats")
    if st:
        l.append(f"⚡ Score: {st['score']}/10 (por profundidad de la caída)")
        l.append(f"📊 Histórico medido: caídas así → {st['retorno_pct']:+.2f}% de media a 2 horas · "
                 f"mediana {st['mediana_pct']:+.2f}% · aciertos {st['aciertos']}% (n={st['n']})")
    l += [f"🎯 Niveles de REFERENCIA (medidos, 3 años, n={REF_NIVELES['rebote'][3]}): "
          f"stop ${fmt_precio(s['sl'])} (−{s.get('risk_pct', 0):.2f}%) · "
          f"objetivo ${fmt_precio(s['tp'])} (+{s.get('tp_pct', 0):.2f}%) · "
          f"stop {REB_SL:g}×ATR / objetivo {REB_TP:g}×ATR, tope {REF_NIVELES['rebote'][2]} velas",
          f"🧭 Horizonte: {s.get('horizonte', '40 velas (horas)')}",
          f"🕐 {time.strftime('%Y-%m-%d %H:%M')} (UTC−4)",
          "⚠️ Es una compra contra el pánico y el costo de ida y vuelta (~0,2%) se come parte de la "
          "ganancia. Medido: +0.15% por señal en 3 años (n=379) con la 2ª ventana en −0.10% → los "
          "niveles son **referencia para seguirla**, no una ventaja probada.", sep]
    return "\n".join(l)


def fmt_precio(x):
    """Formato de precio legible según su magnitud."""
    if x >= 1000:
        return f"{x:,.2f}"
    if x >= 1:
        return f"{x:.4f}"
    if x >= 0.01:
        return f"{x:.6f}"
    return f"{x:.8f}"


def fmt_signal(s, stats):
    """Formato compacto pedido por Dylan (bloques etiquetados + S/R + tipo de divergencia)."""
    short = s["dir"] == "short"
    score = s.get("score")
    etiqueta = (s.get("score_label") or "").upper()
    cabecera = "🔴 DIVERGENCIA BAJISTA" if short else "🟢 DIVERGENCIA ALCISTA"
    sep = "━" * 22
    signo = "−" if short else "+"
    lineas = [cabecera, sep,
              f"📡 Par: {s['sym']}",
              f"⏱ Timeframe: {s['tf']}",
              f"📈 RSI: {s['rsi_now']:.2f} ({s['rsi_prev']:.1f} → {s['rsi_now']:.1f})",
              f"💰 Precio: ${fmt_precio(s['entry'])}"]
    if s.get("adx") is not None:
        fuerza = ("tendencia fuerte" if s["adx"] >= 40 else "tendencia definida" if s["adx"] >= 25
                  else "tendencia débil" if s["adx"] >= 20 else "sin tendencia")
        dom = "vendedores" if s["minus_di"] > s["plus_di"] else "compradores"
        lineas.append(f"💢 ADX: {s['adx']:.1f} ({fuerza} · −DI {s['minus_di']:.1f} vs +DI {s['plus_di']:.1f} "
                      f"→ {dom})")
    if score:
        lineas.append(f"⚡ Score: {score}/10 {etiqueta}")
    sop = "/".join(f"${fmt_precio(x)}" for x in (s.get("soportes") or [])) or "—"
    res = "/".join(f"${fmt_precio(x)}" for x in (s.get("resistencias") or [])) or "—"
    lineas.append(f"🗺 S: {sop} | R: {res}")
    ob_txt = OB.linea(s, s.get("entry"))
    if ob_txt:
        lineas.append(ob_txt)
    lineas.append(f"🎯 Estrategia: {s.get('tipo','Clásica')} | Nivel: "
                  f"{'premium' if s.get('tier') == 'premium' else 'normal'}")
    obv = s.get("obv")
    obv_txt = "" if obv is None else (" · OBV: " + ("distribución" if obv < 0 else "acumulación"))
    lineas.append(f"📊 Volumen: {s['vol_ratio']:.1f}× la media{obv_txt}")
    deriv = linea_derivados(s.get("_deriv"))
    if deriv:
        lineas.append(deriv)
    rr = s.get("tp_pct", 0) / max(s["risk_pct"], 1e-9)
    lineas.append(f"🔔 Entrada ${fmt_precio(s['entry'])} · Stop ${fmt_precio(s['sl'])} "
                  f"({signo}{s['risk_pct']:.2f}%) · Objetivo ${fmt_precio(s['tp'])} "
                  f"({signo}{s.get('tp_pct', TP_MULT * s['risk_pct']):.2f}%) · R:R 1:{rr:.1f}")
    if rr < 0.95:
        lineas.append("⚠️ El soporte está más cerca que el stop (R:R < 1): conviene esperar un rebote "
                      "para vender mejor, o pasar de largo esta señal.")
    lineas.append(f"🕐 {time.strftime('%Y-%m-%d %H:%M')} (UTC−4)")
    ctx = contexto_horario(s.get("entry_ts"))
    if ctx:
        lineas.append(ctx)
    st = s.get("score_stats")
    if st:
        lineas.append(f"📈 Histórico {st['score']}/10 (salida fija 2×ATR/1×ATR): {st['winrate']}% al "
                      f"objetivo · {st['mov_por_señal_pct']:+.2f}% por alerta (n={st['n']})")
    lineas.append(sep)
    return "\n".join(lineas)


# ---------------------------------------------------------------- gráfico de la señal
def _limpiar_graficos():
    """Conserva los PNG más nuevos; borra los viejos (si no, la carpeta crece sin fin)."""
    try:
        rutas = [os.path.join(CHART_DIR, f) for f in os.listdir(CHART_DIR)]
        rutas.sort(key=os.path.getmtime, reverse=True)
        for viejo in rutas[CHART_KEEP:]:
            os.remove(viejo)
    except Exception:
        pass


def con_derivados(s):
    """Adjunta funding e interés abierto a la señal (una sola consulta por par y corrida)."""
    sym = s.get("sym")
    if sym not in _DERIV_CACHE:
        _DERIV_CACHE[sym] = derivados(sym)
    s["_deriv"] = _DERIV_CACHE[sym]
    return s


def contexto_horario(ts_ms):
    """Contexto de hora/día con su histórico medido (candidatas2.py, 333 señales de short con volumen).
    Se etiqueta, no se filtra: Dylan prioriza volumen de señales y decide él."""
    try:
        hora = int(int(ts_ms) / 3_600_000) % 24
        dia = (int(int(ts_ms) / 86_400_000) + 4) % 7
    except Exception:
        return ""
    if hora < 8:
        sesion, mov, n = "Asia (0-7 UTC)", 0.40, 69
    elif hora < 16:
        sesion, mov, n = "Europa (8-15 UTC)", 0.01, 153
    else:
        sesion, mov, n = "América (16-23 UTC)", -0.74, 111
    fin = dia in (5, 6)
    txt = f"🕐 Contexto: {sesion} · medido {mov:+.2f}% por señal (n={n})"
    if fin:
        txt += f" · fin de semana (+0.66%, n=68)"
    if mov < -0.1:
        txt += " ⚠️ contexto flojo medido (puede ser ruido: re-validar con el marcador propio)"
    return txt


def registrar(s, canal=""):
    """Anota la señal enviada para que `tracker.py` pueda seguir su resultado después.
    Sin esto, el server no tiene forma de saber si sus propias señales salieron bien."""
    try:
        fila = {
            "ts": int(time.time() * 1000), "sym": s["sym"], "tf": s["tf"], "dir": s["dir"],
            "tipo": s.get("tipo"), "tier": s.get("tier"), "score": s.get("score"),
            "entry": s.get("entry"), "sl": s.get("sl"), "tp": s.get("tp"),
            "entry_ts": s.get("entry_ts"), "pivot_ts": s.get("pivot_ts"),
            "risk_pct": s.get("risk_pct"), "atr_pct": s.get("atr_pct"),
            "adx": s.get("adx"), "vol_ratio": s.get("vol_ratio"),
            "soportes": s.get("soportes") or [], "flujo": STREAM_LABEL or "principal",
            "canal": canal,
            # estos campos son los que permiten dibujar la divergencia en el gráfico del CIERRE
            "px_prev": s.get("px_prev"), "px_now": s.get("px_now"),
            "rsi_prev": s.get("rsi_prev"), "rsi_now": s.get("rsi_now"),
            "pivot_gap": s.get("pivot_gap"), "score_label": s.get("score_label"),
            "resistencias": s.get("resistencias") or [],
            # bloques de orden (para el gráfico del cierre y para poder medirlos con datos propios)
            "ob_arriba": s.get("ob_arriba"), "ob_abajo": s.get("ob_abajo"),
            # contexto, para que el marcador propio pueda re-validar el efecto hora/día con datos reales
            "hora": int(int(s.get("entry_ts") or 0) / 3_600_000) % 24,
            "dia": (int(int(s.get("entry_ts") or 0) / 86_400_000) + 4) % 7,
        }
        with open(LOG_SENALES, "a") as fh:
            fh.write(json.dumps(fila) + "\n")
    except Exception as e:
        log(f"  ! no se pudo registrar la señal para seguimiento: {e}")


def grafico(s):
    """PNG de la señal, o None si no se puede dibujar (nunca bloquea la alerta)."""
    if not CHART or not s.get("_ks"):
        return None
    try:
        import chart as CH
        os.makedirs(CHART_DIR, exist_ok=True)
        ruta = os.path.join(CHART_DIR, f"{s['sym']}_{s['tf']}_{s.get('pivot_ts') or s.get('entry_ts')}.png")
        CH.render(s, s["_ks"], ruta)
        _limpiar_graficos()
        return ruta
    except Exception as e:
        log(f"  ! sin gráfico para {s.get('sym')} {s.get('tf')}: {type(e).__name__}: {e}")
        return None


# ---------------------------------------------------------------- escaneo
def scan(args, st, dry=False):
    detectar, formatear = {"divergencia": (find_signals, fmt_signal),
                           "momentum": (find_momentum, fmt_momentum),
                           "rebote": (find_rebote, fmt_rebote)}[TIPO]
    stats = measured_stats() if TIPO == "divergencia" else {}
    syms = watchlist(st)
    log(f"escaneo: {len(syms)} pares × {','.join(INTERVALS)} · lado={SIDE} · "
        f"datos={base_datos()} · "
        f"volumen {'ON' if USE_VOL else 'OFF'} · "
        f"ADX {'≥' + format(MIN_ADX, '.0f') if MIN_ADX > 0 else 'OFF'}")
    found, seen, cooldown = [], st["seen"], st["cooldown"]
    for sym in syms:
        for tf in INTERVALS:
            try:
                ks = klines(sym, tf, 300)
            except Exception as e:
                log(f"  ! {sym} {tf}: {e}")
                continue
            for s in detectar(sym, tf, ks):
                key = f"{s['sym']}|{s['tf']}|{s['pivot_ts']}"
                if key in seen:
                    continue
                # OJO CON EL ORDEN (bug encontrado el 10/10): antes se marcaba como "vista" ANTES de
                # chequear el enfriamiento del par, así que una señal que llegaba mientras el mismo par
                # estaba en enfriamiento quedaba marcada para siempre y se perdía. Ahora sólo se marca
                # si de verdad se va a enviar (así sale al expirar el enfriamiento).
                if s["entry_ts"] - cooldown.get(sym, 0) < COOLDOWN_H * 3600_000:
                    continue
                seen[key] = int(time.time() * 1000)
                cooldown[sym] = s["entry_ts"]
                s["_ks"] = ks              # velas para el gráfico (no se guarda en state.json)
                found.append(s)
            time.sleep(0.06)
    total = len(found)
    if MIN_SCORE > 0:
        found = [s for s in found if (s.get("score") or 0) >= MIN_SCORE]
    found.sort(key=lambda s: (-(s.get("score") or 0),
                              0 if s.get("tier") == "premium" else 1, s["tf"]))

    if not found:
        log(f"  sin alertas que mandar (detectadas: {total}, filtradas por puntaje ≥{MIN_SCORE}: "
            f"{total - len(found)})")
        return 0
    if INDIVIDUAL:
        # una señal = un mensaje (como pidió Dylan): se mandan de a una, con una pausa por los
        # límites de Discord (~5 mensajes cada 5 s por canal).
        enviadas = 0
        if dry:                      # ojo: en modo individual el --dry-run NO debe enviar nada
            for s in found[:MAX_ALERTS]:
                print(formatear(s, stats) + "\n")
            return len(found)
        for s in found[:MAX_ALERTS]:
            con_derivados(s)
            registrar(s, DISCORD_OVERRIDE)
            # cada señal a su canal según el puntaje (calidad / media / cantidad)
            _canal = canal_por_puntaje(s.get("score"))
            _nombre = ("calidad/premium" if _canal == CANAL_ALTO and CANAL_ALTO else
                       "media/normales" if _canal == CANAL_MEDIO and CANAL_MEDIO else
                       "cantidad" if _canal == CANAL_BAJO and CANAL_BAJO else "canal por defecto")
            log(f"    {s.get('sym')}/{s.get('tf')} · puntaje {s.get('score')}/10 → {_nombre}")
            deliver(formatear(s, stats), image=grafico(s), canal=_canal)
            enviadas += 1
            if enviadas < len(found):
                time.sleep(1.4)
        if len(found) > MAX_ALERTS:
            deliver(f"…y {len(found) - MAX_ALERTS} señales más (omitidas para no saturar).")
        log(f"  {len(found)} señales enviadas de a una")
        return len(found)
    for s in found[:MAX_ALERTS]:
        con_derivados(s)
    payload = [formatear(s, stats) for s in found[:MAX_ALERTS]]
    if len(found) > MAX_ALERTS:
        payload.append(f"…y {len(found) - MAX_ALERTS} más (omitidas para no saturar).")
    n_prem = sum(1 for s in found if s.get("tier") == "premium")
    n_norm = len(found) - n_prem
    fuertes = sum(1 for s in found if (s.get("score") or 0) >= 7)
    mejor = max((s.get("score") or 0) for s in found)
    if TIPO == "divergencia":
        header = (f"{STREAM_LABEL + ' · ' if STREAM_LABEL else ''}"
                  f"🔔 <b>{len(found)} señal(es)</b> · mejor puntaje <b>{mejor}/10</b> · "
                  f"🎯 {fuertes} con puntaje ≥7 · ⭐ {n_prem} premium · {time.strftime('%H:%M')} UTC")
    else:
        header = (f"{STREAM_LABEL + ' · ' if STREAM_LABEL else ''}"
                  f"🔔 <b>{len(found)} señal(es)</b> · mejor puntaje <b>{mejor}/10</b> · "
                  f"{time.strftime('%H:%M')} UTC")
    text = header + "\n\n" + "\n\n".join(payload)
    log(f"  {len(found)} señales ({n_prem} premium / {n_norm} normal, {fuertes} con puntaje ≥7; "
        f"detectadas {total}): " + ", ".join(f"{s['sym']}/{s['tf']} {s.get('score','?')}/10"
                                            for s in found[:10])
        + (" …" if len(found) > 10 else ""))
    for s in found[:MAX_ALERTS]:
        registrar(s, DISCORD_OVERRIDE)      # agrupado: se registran todas las que van en el mensaje
    if dry:
        print(text)
        return len(found)
    deliver(text, image=grafico(found[0]))   # agrupado: el gráfico de la mejor señal
    return len(found)


def report(args, st, dry=False):
    """Resumen diario: deja claro que el bot sigue vivo aunque no haya alertas."""
    stats = measured_stats()
    syms = st["watchlist"]["syms"] or watchlist(st)
    cfg = (f"divergencia {'bajista' if SIDE == 'short' else SIDE} de RSI(14) · "
           f"salida objetivo {TP_MULT:.0f}×ATR / stop {SL_MULT:.0f}×ATR"
           + (" · sin exigir volumen" if not USE_VOL else ""))
    niveles = ""
    for tf in ("15m", "1h", "4h", "1d"):
        d = stats.get(tf) or {}
        for tier in ("premium", "normal"):
            t = d.get(tier)
            if t and t.get("n", 0) >= 5:
                niveles += (f"   {'⭐' if tier == 'premium' else '📡'} {tf} {tier}: "
                            f"{t['winrate']}% al objetivo, {t['mov_por_señal_pct']:+.2f}% por alerta "
                            f"(n={t['n']}, {t['sigs_mes']}/mes)\n")
    puntajes = ""
    for d in (CALIB or {}).get("deciles", []):
        if d["score"] >= 9 or d["score"] <= 2:
            puntajes += (f"   🎯 {d['score']}/10 {d['etiqueta']}: {d['winrate']}% al objetivo, "
                         f"{d['mov_por_señal_pct']:+.2f}% por alerta (n={d['n']}, {d['sigs_mes']}/mes)\n")
    ctrl = (CALIB or {}).get("control_mitades") or {}
    mitades = ""
    if ctrl:
        mitades = ("   ↔️ control en mitades (puntaje ≥7): "
                   + " · ".join(f"{k} {v['altos_mov']:+.2f}%" for k, v in ctrl.items()) + "\n")
    txt = (f"📋 <b>Bot de alertas · reporte diario</b>\n"
           f"👀 {len(syms)} pares vigilados (top por volumen, sin stablecoins ni apalancados)\n"
           f"⏱ Temporalidades: {', '.join(INTERVALS)}\n"
           f"🔧 {cfg}\n"
           f"⭐ PREMIUM: divergencia + volumen ≥{VOL_MULT}× + ADX ≥ {MIN_ADX:.0f} (tendencia definida)\n"
           f"📡 NORMAL: divergencia + volumen con ADX por debajo de {MIN_ADX:.0f}\n"
           f"🎯 Puntaje 1-10: cada señal se puntúa con la calibración medida"
           + (f" (última: {(CALIB or {}).get('meta', {}).get('generated', '?')}, "
              f"{(CALIB or {}).get('meta', {}).get('n', '?')} señales)" if CALIB else " (sin calibrar)")
           + f"{f' · se envían solo las de puntaje ≥ {MIN_SCORE:g}' if MIN_SCORE > 0 else ''}\n"
           f"📊 Histórico medido:\n{niveles}{puntajes}{mitades}"
           f"📨 Alertas enviadas en 24 h: "
           f"{sum(1 for v in st['seen'].values() if int(time.time()*1000) - v < 86_400_000)}\n"
           f"ℹ️ Solo informa: no opera ni pide claves de exchange.")
    if dry:
        print(txt)
        return 0
    deliver(txt)
    return 0


def main():
    global USE_VOL, MIN_ADX, SIDE, INTERVALS, FRESH_BARS, WATCHLIST_N, MIN_ATR_PCT, \
        NORMAL_SIN_VOLUMEN, NORMAL_INTERVALS, MIN_SCORE, SL_MULT, TARGET_MODE, \
        DISCORD_OVERRIDE, STREAM_LABEL, STATE_PATH, SEND_TELEGRAM, INDIVIDUAL, TIPO, CHART
    global CANAL_ALTO, CANAL_MEDIO, CANAL_BAJO
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="imprime en pantalla, no manda a Telegram")
    ap.add_argument("--report", action="store_true", help="manda el resumen diario en vez de escanear")
    ap.add_argument("--side", default=SIDE, choices=["short", "long", "both"],
                    help="lado a alertar (por defecto: short)")
    ap.add_argument("--min-adx", type=float, default=MIN_ADX,
                    help="ADX mínimo para alertar; 0 = sin filtro de ADX (por defecto 25)")
    ap.add_argument("--intervals", default=",".join(INTERVALS),
                    help="temporalidades separadas por coma (por defecto 1h,4h,1d)")
    ap.add_argument("--no-vol", action="store_true", help="no exigir volumen ≥1.2× la media")
    ap.add_argument("--fresh-bars", type=int, default=FRESH_BARS,
                    help="cuántas velas cerradas puede tener la señal para seguir siendo nueva")
    ap.add_argument("--no-state", action="store_true",
                    help="no lee ni guarda state.json (pruebas: no marca alertas como vistas)")
    ap.add_argument("--top", type=int, default=WATCHLIST_N,
                    help="cuántos pares vigilar, por volumen 24 h (por defecto 20; hay ~740 pares USDT)")
    ap.add_argument("--min-atr-pct", type=float, default=MIN_ATR_PCT,
                    help="ATR mínimo como %% del precio (0 = sin filtro; 1.5 hace rentable el 15m)")
    ap.add_argument("--normal-sin-volumen", action="store_true",
                    help="el nivel NORMAL no exige volumen (muchas más alertas, valor medido negativo)")
    ap.add_argument("--normal-intervals", default="",
                    help="temporalidades donde SÍ se permite el nivel normal (vacío = todas). "
                         "Medido: el nivel normal solo aporta en 15m, así que '15m' es lo recomendable")
    ap.add_argument("--min-score", type=float, default=0,
                    help="puntaje mínimo (1-10) para enviar la alerta; 0 = manda todas (volumen)")
    ap.add_argument("--stop-atr", type=float, default=SL_MULT,
                    help="stop en múltiplos de ATR (medido: 1.5 rinde mejor que 1.0)")
    ap.add_argument("--target", default=TARGET_MODE, choices=["soporte", "atr"],
                    help="objetivo: en el soporte real (medido como el menos malo) o 2×ATR fijo")
    ap.add_argument("--canal-alto", default="", help="canal para las señales de puntaje alto (calidad)")
    ap.add_argument("--canal-medio", default="", help="canal para las de puntaje medio")
    ap.add_argument("--canal-bajo", default="", help="canal para las de puntaje bajo (cantidad)")
    ap.add_argument("--discord-channel", default="",
                    help="ID de canal de Discord para este flujo (vacío = el canal de señales normal)")
    ap.add_argument("--label", default="",
                    help="etiqueta del flujo (p. ej. RADAR): encabeza los mensajes y usa su propio "
                         "archivo de estado, así no compite con el flujo principal")
    ap.add_argument("--no-telegram", action="store_true",
                    help="este flujo NO va a Telegram (para flujos de volumen alto)")
    ap.add_argument("--individual", action="store_true",
                    help="una señal = un mensaje, en vez de agrupar varias en un solo mensaje")
    ap.add_argument("--no-chart", action="store_true",
                    help="alertas sin el gráfico PNG adjunto (por defecto SÍ se adjunta)")
    ap.add_argument("--tipo", default=TIPO, choices=["divergencia", "momentum", "rebote"],
                    help="tipo de señal: divergencia (short) · momentum (nuevo máximo de 30 días) · "
                         "rebote (caída ≥5%% en 1h con volumen)")
    args = ap.parse_args()

    SIDE = args.side
    TIPO = args.tipo
    MIN_ADX = args.min_adx
    MIN_ATR_PCT = args.min_atr_pct
    MIN_SCORE = args.min_score
    SL_MULT = args.stop_atr
    TARGET_MODE = args.target
    if args.discord_channel:
        DISCORD_OVERRIDE = args.discord_channel
    CANAL_ALTO = args.canal_alto or ""
    CANAL_MEDIO = args.canal_medio or ""
    CANAL_BAJO = args.canal_bajo or ""
    if args.label:
        STREAM_LABEL = args.label.upper()
        STATE_PATH = os.path.join(HERE, f"state_{args.label.lower()}.json")
    WATCHLIST_N = max(1, args.top)
    if args.no_telegram:
        SEND_TELEGRAM = False
    if args.individual:
        INDIVIDUAL = True
    if args.no_chart:
        CHART = False
    if args.no_vol:
        USE_VOL = False
    if args.normal_sin_volumen:
        NORMAL_SIN_VOLUMEN = True
    NORMAL_INTERVALS = ([x.strip() for x in args.normal_intervals.split(",") if x.strip()]
                        or None)
    INTERVALS = [x.strip() for x in args.intervals.split(",") if x.strip()]
    FRESH_BARS = max(1, args.fresh_bars)

    os.makedirs(LOG_DIR, exist_ok=True)
    if args.no_state:
        st = {"watchlist": {"ts": 0, "syms": []}, "seen": {}, "cooldown": {}}
        if args.report:
            report(args, st, args.dry_run)
        else:
            scan(args, st, args.dry_run)
        return
    st = load_state()
    try:
        if args.report:
            report(args, st, args.dry_run)
        else:
            scan(args, st, args.dry_run)
    finally:
        save_state(st)


if __name__ == "__main__":
    main()
