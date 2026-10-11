#!/usr/bin/env python3
"""Demo: busca una señal real y le dibuja el gráfico. No manda nada."""
import os
import sys
import time

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.chdir(RAIZ)
import signals as S
import chart as CH

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "charts")
os.makedirs(OUT, exist_ok=True)

# barrido amplio: sirve para el demo (agarrar alguna señal real reciente para mirar el gráfico)
S.FRESH_BARS = 200
try:
    candidatos = [(s, ["15m", "1h", "4h"]) for s in S.top_symbols(20)]
except Exception as ex:
    print("no se pudo leer el top por volumen:", ex)
    candidatos = [("NEARUSDT", ["15m", "1h", "4h"]), ("ENAUSDT", ["4h", "1h"]),
                  ("AVAXUSDT", ["15m", "1h", "4h"]), ("BANKUSDT", ["1h"]), ("GUSDT", ["1h"])]

encontradas = []
for sym, tfs in candidatos:
    for tf in tfs:
        try:
            ks = S.klines(sym, tf, 300)
        except Exception as ex:
            print(f"  ! {sym} {tf}: {ex}")
            continue
        for s in S.find_signals(sym, tf, ks):
            s["_ks"] = ks
            encontradas.append(s)
        time.sleep(0.08)

encontradas.sort(key=lambda s: -s["entry_ts"])
print(f"señales encontradas: {len(encontradas)}")
for s in encontradas[:6]:
    print(f"  {s['sym']:>9} {s['tf']:>3} · puntaje {s.get('score')} · {s['tier']:>7} · "
          f"RSI {s['rsi_prev']:.1f}→{s['rsi_now']:.1f} · pivotes {s['pivot_gap']} velas · "
          f"entrada {s['entry']:.4f}")

if not encontradas:
    raise SystemExit("sin señales en los pares de prueba (probá con más pares)")

# se grafican hasta 3 (para mirarlas y elegir la del demo)
for s in encontradas[:3]:
    path = os.path.join(OUT, f"{s['sym']}_{s['tf']}_demo.png")
    try:
        CH.render(s, s["_ks"], path)
        print(f"PNG → {path} ({os.path.getsize(path)/1024:.0f} KB)")
        # control: el RSI que dibuja el gráfico debe coincidir con el de la señal (mismo indicador)
        ch = CH.rsi_serie([float(c[4]) for c in s["_ks"][:-1]])
        cerr = s["_ks"][:-1][-CH.BARS:]
        des = len(s["_ks"][:-1]) - len(cerr)
        p2 = next((i for i, c in enumerate(cerr) if int(c[0]) == int(s["pivot_ts"])), None)
        if p2 is not None:
            print(f"     control RSI pivote: gráfico {ch[des+p2]:.2f} vs señal {s['rsi_now']:.2f}")
    except Exception as ex:
        print(f"!! falló el gráfico de {s['sym']} {s['tf']}: {type(ex).__name__}: {ex}")
