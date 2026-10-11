#!/usr/bin/env python3
"""SEGUIMIENTO de las señales: ¿llegaron al objetivo o saltó el stop?

Sin esto el server tira flechas y nunca dice cómo terminaron. Lee `signals_log.jsonl` (lo escribe
`signals.py` al enviar cada alerta), evalúa cada señal contra las velas posteriores de Binance y:
  · publica el cierre en el canal de resultados
  · guarda el resultado en `results.jsonl` (marcador acumulado)
  · `--resumen` arma el marcador y lo compara con el backtest

Reglas — LAS MISMAS del backtest, para que las cifras sean comparables:
  · se evalúa vela por vela desde la vela siguiente a la entrada
  · si una vela toca objetivo y stop a la vez, cuenta STOP (convención conservadora)
  · máximo `--max-bars` velas (50 por defecto): si no tocó nada, cierra 'por tiempo' al cierre
  · sin objetivo/stop (momentum y rebote de hoy) no se sigue: queda 'sin_niveles'

No opera nada: solo mira precios y publica resultados.
"""
import argparse
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import signals as S

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = S.LOG_SENALES
RESULTADOS = os.path.join(HERE, "results.jsonl")
ESTADO_WD = os.path.join(HERE, ".tracker_estado.json")
CANAL_RESULTADOS = "1551671378065752255"   # #resultados-señales (--canal lo cambia)
ESTADO_SEG = os.path.join(HERE, ".seguimiento_estado.json")
MAX_BARS = 50
MINUTOS_VELAS = {"5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}


def log(msg):
    """Toda salida del tracker con hora: si no, el propio vigilante no puede verificar
    si corrió (fue un falso positivo de la auditoría)."""
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


def leer_jsonl(path):
    filas = []
    if os.path.exists(path):
        for linea in open(path):
            linea = linea.strip()
            if linea:
                try:
                    filas.append(json.loads(linea))
                except Exception:
                    pass
    return filas


def clave(f):
    return f"{f.get('sym')}|{f.get('tf')}|{f.get('pivot_ts')}"


def evaluar(f, ks, max_bars=MAX_BARS):
    """Devuelve el resultado de la señal o None si sigue abierta."""
    tp, sl, entry, ts = f.get("tp"), f.get("sl"), f.get("entry"), f.get("entry_ts")
    if not tp or not sl or not entry or not ts:
        return {"estado": "sin_niveles", "mov_pct": 0.0, "velas": 0}
    corto = (f.get("dir") or "short") == "short"
    futuras = [c for c in ks if int(c[0]) > int(ts)]
    if not futuras:
        return None
    for j, c in enumerate(futuras[:max_bars], 1):
        hi, lo, cl = float(c[2]), float(c[3]), float(c[4])
        if corto:
            tocó_stop, tocó_tp = hi >= float(sl), lo <= float(tp)
        else:
            tocó_stop, tocó_tp = lo <= float(sl), hi >= float(tp)
        if tocó_stop:                      # empate en la misma vela: gana el stop (conservador)
            return {"estado": "stop", "mov_pct": -abs(float(sl) - float(entry)) / float(entry) * 100,
                    "velas": j, "cierre": float(sl), "cierre_ts": int(c[0])}
        if tocó_tp:
            return {"estado": "objetivo", "mov_pct": abs(float(entry) - float(tp)) / float(entry) * 100,
                    "velas": j, "cierre": float(tp), "cierre_ts": int(c[0])}
    if len(futuras) >= max_bars:
        ult = futuras[max_bars - 1]
        mov = (float(entry) - float(ult[4])) / float(entry) * 100 if corto else \
              (float(ult[4]) - float(entry)) / float(entry) * 100
        return {"estado": "por tiempo", "mov_pct": mov, "velas": max_bars,
                "cierre": float(ult[4]), "cierre_ts": int(ult[0])}
    return None                            # sigue abierta


def grafico_resultado(f, ks, r):
    """PNG del cierre: el mismo gráfico de la alerta, con el recorrido y el veredicto marcados.
    Devuelve None si no se puede dibujar (nunca bloquea el mensaje)."""
    try:
        import chart as CH
        os.makedirs(S.CHART_DIR, exist_ok=True)
        ruta = os.path.join(S.CHART_DIR, f"cierre_{f['sym']}_{f['tf']}_{f.get('pivot_ts') or f.get('entry_ts')}.png")
        CH.render(f, ks, ruta, resultado=r)
        return ruta
    except Exception as e:
        log(f"  ! sin gráfico de cierre para {f.get('sym')} {f.get('tf')}: {type(e).__name__}: {e}")
        return None


def grafico_marcador(resultados, dias=None):
    """PNG con una barra por señal cerrada (verde objetivo / rojo stop)."""
    try:
        import chart as CH
        os.makedirs(S.CHART_DIR, exist_ok=True)
        ruta = os.path.join(S.CHART_DIR, "marcador.png")
        CH.render_marcador(resultados, ruta, dias=dias)
        return ruta
    except Exception as e:
        log(f"  ! sin marcador gráfico: {type(e).__name__}: {e}")
        return None


def avisos_seguimiento(f, ks, umbral=0.75):
    """Avisos de una señal ABIERTA: se acerca al objetivo, se acerca al stop, o se invalidó.
    Devuelve [(tipo, texto)] — cada tipo se manda una sola vez por señal (lo controla el estado)."""
    tp, sl, entry, ts = f.get("tp"), f.get("sl"), f.get("entry"), f.get("entry_ts")
    if not (tp and sl and entry and ts):
        return []
    # Si la señal YA se resolvió (tocó objetivo o stop), no hay nada que avisar: sin esta guarda
    # los avisos disparaban con porcentajes absurdos (1149% del camino) en señales ya cerradas.
    if evaluar(f, ks) is not None:
        return []
    corto = (f.get("dir") or "short") == "short"
    futuras = [c for c in ks if int(c[0]) > int(ts)]
    if not futuras:
        return []
    precio = float(futuras[-1][4])
    abierta_h = MINUTOS_VELAS.get(f.get("tf"), 0) * len(futuras) / 60
    nivel = "⭐ premium" if f.get("tier") == "premium" else f"📡 {f.get('tier')}"
    cab = f"**{f.get('sym')} {f.get('tf')}**"
    atr = (float(f.get("atr_pct") or 0) / 100) * float(entry)
    salida = []

    dist_tp = abs(float(entry) - float(tp))
    dist_sl = abs(float(sl) - float(entry))
    progreso = ((float(entry) - precio) / dist_tp) if corto else ((precio - float(entry)) / dist_tp)
    en_contra = ((precio - float(entry)) / dist_sl) if corto else ((float(entry) - precio) / dist_sl)

    if progreso >= umbral:
        falta = abs(precio - float(tp)) / precio * 100
        salida.append(("cerca_objetivo",
                       f"⚠️ **CERCA DEL OBJETIVO** ({progreso * 100:.0f}% del camino) · {nivel} · {cab}\n"
                       f"📉 entrada {S.fmt_precio(entry)} → ahora {S.fmt_precio(precio)} · falta "
                       f"**{falta:.2f}%** para el objetivo ({S.fmt_precio(tp)}) · {abierta_h:.1f} h abierta"))
    if en_contra >= umbral:
        falta = abs(float(sl) - precio) / precio * 100
        salida.append(("cerca_stop",
                       f"🛑 **CERCA DEL STOP** ({en_contra * 100:.0f}% del camino) · {nivel} · {cab}\n"
                       f"📈 entrada {S.fmt_precio(entry)} → ahora {S.fmt_precio(precio)} · el stop está a "
                       f"**{falta:.2f}%** ({S.fmt_precio(sl)}) · {abierta_h:.1f} h abierta"))
    if atr and progreso < umbral and en_contra < umbral:
        margen = 0.5 * atr
        if (corto and precio > float(entry) + margen) or (not corto and precio < float(entry) - margen):
            salida.append(("invalidada",
                           f"🚫 **DIVERGENCIA INVALIDADA** · {nivel} · {cab}\n"
                           f"📈 el precio volvió sobre la entrada ({S.fmt_precio(precio)} vs "
                           f"{S.fmt_precio(entry)}) → la lectura {'bajista' if corto else 'alcista'} ya no se "
                           f"sostiene · stop intacto en {S.fmt_precio(sl)} · {abierta_h:.1f} h abierta"))
    return salida


def duracion(f, velas):
    minutos = MINUTOS_VELAS.get(f.get("tf"), 0) * velas
    if minutos >= 1440:
        return f"{minutos / 1440:.1f} días"
    if minutos >= 60:
        return f"{minutos / 60:.1f} h"
    return f"{minutos} min"


def _velas(n):
    return "vela" if n == 1 else "velas"


def texto_cierre(f, r):
    icono = {"objetivo": "✅", "stop": "❌", "por tiempo": "⏳", "sin_niveles": "❔"}[r["estado"]]
    nivel = "⭐ premium" if (f.get("tier") == "premium") else f"📡 {f.get('tier')}"
    sentido = "a favor" if r["mov_pct"] > 0 else "en contra"
    precio = S.fmt_precio(f.get("entry"))
    cierre = S.fmt_precio(r.get("cierre"))
    # El lado sale de la señal (`dir`), no de un texto fijo: antes TODAS las señales decían "short
    # desde ..." aunque fueran long (se veía en los cierres de momentum/rebote, que son largos).
    corto = (f.get("dir") or "short") == "short"
    lineas = [f"{icono} **{r['estado'].upper()}** · {nivel} · **{f.get('sym')} {f.get('tf')}**",
              f"{'📉 short' if corto else '📈 long'} desde {precio} → cerró en {cierre} "
              f"(**{r['mov_pct']:+.2f}% {sentido}**)",
              f"⏱ {duracion(f, r['velas'])} ({r['velas']} {_velas(r['velas'])}) · puntaje {f.get('score')}/10 · "
              f"flujo {f.get('flujo')}"]
    return "\n".join(lineas)


def marcador(resultados, dias=30):
    desde = int(time.time() * 1000) - dias * 86_400_000
    recientes, vistas = [], set()
    for r in resultados:
        if r.get("reportado_ts", 0) < desde:
            continue
        # El MISMO par|tf|pivote puede haber salido por dos flujos (premium y normales): en las
        # cuentas va una sola vez, si no el marcador duplica las señales.
        k = r.get("clave")
        if k in vistas:
            continue
        vistas.add(k)
        recientes.append(r)
    utiles = [r for r in recientes if r["estado"] != "sin_niveles"]
    if not utiles:
        return None, recientes
    n = len(utiles)
    gana = sum(1 for r in utiles if r["estado"] == "objetivo")
    mov = sum(r["mov_pct"] for r in utiles) / n
    return {"n": n, "objetivo": gana, "stop": sum(1 for r in utiles if r["estado"] == "stop"),
            "tiempo": sum(1 for r in utiles if r["estado"] == "por tiempo"),
            "pct_objetivo": 100 * gana / n, "mov_pct": mov}, recientes


def backtest_de(f):
    """Histórico medido del mismo TF y nivel (para comparar contra lo que el backtest prometía)."""
    try:
        d = json.load(open(S.SHORTS_RESULTS))["tiers"]
        t = d[f["tf"]]["premium" if f.get("tier") == "premium" else "normal"]
        return t.get("mov_por_señal_pct")
    except Exception:
        return None


def resumen(dias=30, resultados=None, enviar=True):
    resultados = resultados if resultados is not None else leer_jsonl(RESULTADOS)
    m, recientes = marcador(resultados, dias)
    if not m:
        txt = f"📊 **Marcador de {dias} días** · todavía no hay señales cerradas que seguir."
        if enviar:
            S.deliver(txt)
        return txt
    por_tf = {}
    for r in recientes:
        if r["estado"] != "sin_niveles":
            por_tf.setdefault((r["tf"], r.get("tier")), []).append(r)
    lineas = [f"📊 **MARCADOR DEL SERVER · últimos {dias} días**",
              f"🎯 {m['objetivo']} al objetivo · ❌ {m['stop']} al stop · ⏳ {m['tiempo']} por tiempo "
              f"· **{m['pct_objetivo']:.0f}% al objetivo** (n={m['n']})",
              f"📈 **{m['mov_pct']:+.2f}% de movimiento medio por alerta** (neto de comisión no incluido)"]
    for (tf, tier), filas in sorted(por_tf.items()):
        g = sum(1 for r in filas if r["estado"] == "objetivo")
        mv = sum(r["mov_pct"] for r in filas) / len(filas)
        prometido = backtest_de({"tf": tf, "tier": tier})
        extra = (f" · backtest de ese nivel: {prometido:+.2f}%" if prometido is not None else "")
        lineas.append(f"   {tf} {tier}: {g}/{len(filas)} al objetivo · {mv:+.2f}% por alerta{extra}")
    lineas.append("ℹ️ Medición propia del server (no del backtest con datos viejos). Cuenta grande "
                  "= más confiable; con n chico, ruido.")
    # desglose por sesión horaria: solo cuando haya muestra suficiente (el efecto se midió en 333
    # señales históricas; acá se re-valida con los cierres propios del server)
    por_sesion = {}
    for r in recientes:
        if r.get("estado") == "sin_niveles" or r.get("hora") is None:
            continue
        h = r["hora"]
        nombre = "Asia (0-7)" if h < 8 else "Europa (8-15)" if h < 16 else "América (16-23)"
        por_sesion.setdefault(nombre, []).append(r)
    for nombre, filas in sorted(por_sesion.items()):
        if len(filas) >= 5:
            g = sum(1 for r in filas if r["estado"] == "objetivo")
            mv = sum(r["mov_pct"] for r in filas) / len(filas)
            lineas.append(f"   sesión {nombre}: {g}/{len(filas)} al objetivo · {mv:+.2f}% por alerta")
    txt = "\n".join(lineas)
    img = grafico_marcador(recientes, dias)
    if enviar:
        S.deliver(txt, img)
    else:
        log(f"[gráfico del marcador: {img}]")
    return txt


def main():
    ap = argparse.ArgumentParser(description="Seguimiento de las señales enviadas")
    ap.add_argument("--dry-run", action="store_true", help="no envía: imprime lo que cerraría")
    ap.add_argument("--resumen", action="store_true", help="manda el marcador acumulado")
    ap.add_argument("--dias", type=int, default=30, help="ventana del marcador (por defecto 30)")
    ap.add_argument("--max-bars", type=int, default=MAX_BARS,
                    help="velas máximas a esperar antes de cerrar 'por tiempo' (por defecto 50)")
    ap.add_argument("--canal", default="", help="ID de canal de Discord para los cierres")
    ap.add_argument("--telegram", action="store_true", help="mandar también a Telegram")
    ap.add_argument("--ejemplos", type=int, default=0,
                    help="prueba: evalúa N señales reales recientes y las muestra (no toca los archivos)")
    args = ap.parse_args()

    if args.canal:
        S.DISCORD_OVERRIDE = args.canal
    elif not args.ejemplos:
        S.DISCORD_OVERRIDE = CANAL_RESULTADOS
    if not args.telegram:
        S.SEND_TELEGRAM = False

    if args.resumen:
        print(resumen(args.dias, enviar=not args.dry_run))
        return

    if args.ejemplos:
        # modo prueba: señales reales del mercado, evaluadas como si se hubieran enviado
        S.FRESH_BARS = 200
        halladas = []
        for sym in S.top_symbols(20):
            for tf in ("15m", "1h", "4h"):
                try:
                    ks = S.klines(sym, tf, 300)
                except Exception:
                    continue
                for s in S.find_signals(sym, tf, ks):
                    s["_ks"] = ks
                    halladas.append(s)
                time.sleep(0.05)
        halladas.sort(key=lambda s: -s["entry_ts"])
        mostradas = 0
        for s in halladas:
            r = evaluar(s, s["_ks"], args.max_bars)
            if not r or r["estado"] == "sin_niveles":
                continue
            txt = "🧪 **EJEMPLO del seguimiento** (así se verá el cierre de cada señal)\n\n" \
                  + texto_cierre(s, r)
            print(txt.replace("\n", " | ")[:200])
            if not args.dry_run:
                S.deliver(txt)
                time.sleep(1.4)
            mostradas += 1
            if mostradas >= args.ejemplos:
                break
        print(f"ejemplos mostrados: {mostradas} de {len(halladas)} señales evaluadas")
        return

    # --- modo normal: revisar las señales abiertas y publicar las que cerraron
    señales = leer_jsonl(LOG)
    resultados = leer_jsonl(RESULTADOS)
    ya = {r.get("clave") for r in resultados}
    pendientes = [f for f in señales if clave(f) not in ya and f.get("denegada") is not True]
    if not pendientes:
        log(f"sin señales pendientes ({len(señales)} registradas, {len(resultados)} resueltas)")
        return
    nuevas = 0
    avisados = 0
    cache = {}
    try:
        seg_estado = json.load(open(ESTADO_SEG))
    except Exception:
        seg_estado = {}
    for f in pendientes:
        kk = f"{f['sym']}|{f['tf']}"
        if kk not in cache:
            try:
                cache[kk] = S.klines(f["sym"], f["tf"], 300)
            except Exception as e:
                log(f"  ! {kk}: {e}")
                cache[kk] = None
        if not cache[kk]:
            continue
        if clave(f) in ya:
            continue                      # ya resuelto en esta misma corrida (salía por dos flujos)
        r = evaluar(f, cache[kk], args.max_bars)
        if not r:
            # sigue abierta → ¿se acerca al objetivo, al stop, o se invalidó?
            for tipo, txt in avisos_seguimiento(f, cache[kk]):
                marca = f"{clave(f)}|{tipo}"
                if seg_estado.get(marca):
                    continue
                seg_estado[marca] = int(time.time() * 1000)
                if args.dry_run:
                    print(txt + "\n")
                else:
                    S.deliver(txt)
                    time.sleep(1.4)
                avisados += 1
            continue
        r.update({"clave": clave(f), "sym": f["sym"], "tf": f["tf"], "dir": f.get("dir"),
                  "tier": f.get("tier"), "score": f.get("score"), "flujo": f.get("flujo"),
                  "entry": f.get("entry"), "tp": f.get("tp"), "sl": f.get("sl"),
                  "entry_ts": f.get("entry_ts"), "tipo": f.get("tipo"),
                  "hora": f.get("hora"), "dia": f.get("dia"),
                  "reportado_ts": int(time.time() * 1000)})
        if not args.dry_run:                  # una prueba NO debe escribir resultados
            with open(RESULTADOS, "a") as fh:
                fh.write(json.dumps(r) + "\n")
        resultados.append(r)
        ya.add(r["clave"])
        nuevas += 1
        if r["estado"] == "sin_niveles":
            continue
        m, _ = marcador(resultados, args.dias)
        pie = ""
        if m:
            pie = (f"\n\n📊 Marcador de {args.dias} días: {m['objetivo']} al objetivo / {m['stop']} al stop "
                   f"· {m['pct_objetivo']:.0f}% · {m['mov_pct']:+.2f}% por alerta (n={m['n']})")
        txt = texto_cierre(f, r) + pie
        img = grafico_resultado(f, cache[kk], r)
        if args.dry_run:
            print(txt + f"\n[gráfico: {img}]\n")
        else:
            S.deliver(txt, img)
            time.sleep(1.4)
    log(f"cierres publicados: {nuevas} · avisos de seguimiento: {avisados} · "
        f"pendientes revisadas: {len(pendientes)}")
    if not args.dry_run:          # el estado de avisos tampoco se toca en una prueba
        json.dump(seg_estado, open(ESTADO_SEG, "w"), indent=1)


if __name__ == "__main__":
    main()
