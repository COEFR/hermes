import os
#!/usr/bin/env python3
"""Genera y (opcionalmente) publica los gráficos de resultado: el marcador + cierres reales."""
import json, os, sys

RAIZ = os.path.dirname(os.path.abspath(__file__))
if os.path.basename(RAIZ) == "dev":
    RAIZ = os.path.dirname(RAIZ)          # los scripts de dev/ viven un nivel abajo
sys.path.insert(0, RAIZ)
os.chdir(RAIZ)
import signals as S
import tracker as TR
import chart as CH

filas = TR.leer_jsonl("results.jsonl")
print(f"cierres en results.jsonl: {len(filas)}")
for f in filas:
    print(f"  {f['estado']:>10} · {f['mov_pct']:+6.2f}% · {f['sym']:>9} {f['tf']:>3} · "
          f"puntaje {f.get('score')} · {f.get('flujo')}")

# --- 1. marcador de conjunto
ruta_marc = CH.render_marcador(filas, "charts/_marcador_demo.png", dias=30)
print(f"\nmarcador: {ruta_marc} ({os.path.getsize(ruta_marc) // 1024} KB)")

# --- 2. dos cierres reales: el puntaje 10 que falló y el que llegó al objetivo
peor = max((f for f in filas if f["estado"] == "stop" and (f.get("score") or 0) >= 7),
           key=lambda f: f.get("score") or 0, default=None)
mejor = next((f for f in filas if f["estado"] == "objetivo"), None)
elegidos = [f for f in (peor, mejor) if f]
for f in elegidos:
    ks = S.klines(f["sym"], f["tf"], 300)
    ruta = TR.grafico_resultado(f, ks, f)
    print(f"cierre {f['sym']} {f['tf']} ({f['estado']}, score {f.get('score')}): {ruta} "
          f"({os.path.getsize(ruta) // 1024} KB)" if ruta else "falló el gráfico")

if "--enviar" in sys.argv:
    S.SEND_TELEGRAM = False
    S.DISCORD_OVERRIDE = os.environ.get("DISCORD_CANAL", "")      # #hermes
    if elegidos:
        f = elegidos[0]
        txt = ("🧪 **EJEMPLO: así se verá el cierre con gráfico** (el recorrido entrada→salida, el "
               "veredicto arriba y las velas posteriores atenuadas)\n\n" + TR.texto_cierre(f, f)
               if False else
               "🧪 **EJEMPLO 1/2 · cierre con gráfico** (recorrido entrada→salida, veredicto arriba, "
               "velas posteriores atenuadas)\n\n" + TR.texto_cierre(f, f))
        print("envío 1:", S.deliver(txt, TR.grafico_resultado(f, S.klines(f["sym"], f["tf"], 300), f)))
    print("envío 2 (marcador):", S.deliver(
        "🧪 **EJEMPLO 2/2 · marcador visual** — cada barra es una señal cerrada: verde llegó al "
        "objetivo, rojo saltó el stop. Acá se ve de un vistazo cuáles fallaron y cuánto.", ruta_marc))
