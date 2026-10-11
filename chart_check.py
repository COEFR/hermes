#!/usr/bin/env python3
"""Control del gráfico: el RSI que se dibuja en los pivotes tiene que ser EXACTAMENTE el de la señal,
los pivotes tienen que caer dentro de la ventana y el PNG tiene que salir.

Correr después de tocar `chart.py`. Cualquier falla acá significa que el gráfico miente.
"""
import os, sys, time

RAIZ = os.path.dirname(os.path.abspath(__file__))
if os.path.basename(RAIZ) == "dev":
    RAIZ = os.path.dirname(RAIZ)          # los scripts de dev/ viven un nivel abajo
sys.path.insert(0, RAIZ)
os.chdir(RAIZ)
import signals as S
import chart as CH

S.FRESH_BARS = 200
fallos = 0
probadas = 0
for sym, tf in (("ADAUSDT", "15m"), ("ENAUSDT", "1h"), ("SOLUSDT", "4h"), ("UNIUSDT", "1h")):
    try:
        ks = S.klines(sym, tf, 300)
    except Exception as e:
        print(f"  ! {sym} {tf}: {e}")
        continue
    sigs = S.find_signals(sym, tf, ks)[:3]
    for s in sigs:
        s["_ks"] = ks
        probadas += 1
        todas = ks[:-1]
        cer, ini = CH._ventana(todas, s, True)
        rsi = CH.rsi_serie([float(c[4]) for c in todas])[ini:ini + len(cer)]
        p2 = next((i for i, c in enumerate(cer) if int(c[0]) == int(s["pivot_ts"])), None)
        p1 = p2 - s["pivot_gap"] if p2 is not None else None
        ok_piv = p1 is not None and 0 <= p1 < len(cer)
        ok_rsi = ok_piv and abs(rsi[p1] - s["rsi_prev"]) < 0.05 and abs(rsi[p2] - s["rsi_now"]) < 0.05
        ruta = f"charts/_check_{sym}_{tf}.png"
        try:
            CH.render(s, ks, ruta)
            ok_png = os.path.getsize(ruta) > 5000
        except Exception as e:
            ok_png = False
            print(f"  !! render {sym} {tf}: {type(e).__name__}: {e}")
        estado = "OK" if (ok_piv and ok_rsi and ok_png) else "FALLA"
        if estado == "FALLA":
            fallos += 1
        print(f"  {estado} {sym:>9} {tf:>3} · ventana {len(cer)} velas (inicio {ini}) · "
              f"pivotes {p1}/{p2} · RSI dibujado {rsi[p1]:.2f}/{rsi[p2]:.2f} vs señal "
              f"{s['rsi_prev']:.2f}/{s['rsi_now']:.2f} · PNG {'sí' if ok_png else 'NO'}")
        if os.path.exists(ruta):
            os.remove(ruta)
    time.sleep(0.1)
print(f"\nseñales probadas: {probadas} · fallas: {fallos}")
sys.exit(1 if fallos else 0)
