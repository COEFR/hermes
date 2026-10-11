import os
#!/usr/bin/env python3
"""SONDA DE ENTREGA desde el runner de GitHub: el paso que colgó el ciclo 25 minutos.

Genera un **gráfico real** con el código de producción y lo **sube** a Discord (multipart, igual que
las alertas) y a Telegram (sendPhoto), midiendo cuánto tarda cada subida y si sale bien.
Manda el resumen por Telegram.

Uso: python3 dev/sonda_entrega.py     (necesita Pillow instalado)
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.chdir(RAIZ)
import chart as CH                    # noqa: E402
import signals as S                   # noqa: E402

lineas = []


def anota(txt):
    print("  " + txt, flush=True)
    lineas.append(txt)


print("=== SONDA DE ENTREGA (gráfico + subidas) desde el runner ===", flush=True)

ruta = "/tmp/sonda_grafico.png"
try:
    ks = S.klines("BTCUSDT", "4h", 300)
    base = float(ks[-2][4])
    sim = {"sym": "BTCUSDT", "tf": "4h", "dir": "short", "tipo": "Clásica bajista",
           "entry": base, "sl": base * 1.02, "tp": base * 0.97, "risk_pct": 2.0, "atr_pct": 1.5,
           "adx": 30.0, "plus_di": 20.0, "minus_di": 26.0, "rsi_now": 58.0, "rsi_prev": 71.0,
           "vol_ratio": 1.5, "pivot_gap": 10, "pivot_ts": int(ks[-14][0]),
           "entry_ts": int(ks[-2][0]), "score": 7, "score_label": "buena", "tier": "premium",
           "soportes": [],
           "ob_arriba": {"lo": base * 1.01, "hi": base * 1.03, "estado": "fresco", "fuerza": 2.0,
                         "ts": int(ks[-40][0])},
           "ob_abajo": {"lo": base * 0.95, "hi": base * 0.96, "estado": "fresco", "fuerza": 2.0,
                        "ts": int(ks[-80][0])}}
    t0 = time.time()
    CH.render(sim, ks, ruta)
    anota(f"gráfico generado: {os.path.getsize(ruta) / 1024:.0f} KB en "
          f"{(time.time() - t0) * 1000:.0f} ms (CPU del runner)")
except Exception as e:
    anota(f"gráfico: FALLÓ ({type(e).__name__}: {e})")

env = S.env_file()
print("\n-- subida a Discord (#hermes) con multipart, como las alertas --", flush=True)
S.SEND_TELEGRAM = False
S.DISCORD_OVERRIDE = os.environ.get("DISCORD_CANAL", "")
t0 = time.time()
try:
    ok = S.send_discord("🔬 Sonda de entrega: imagen subida DESDE el runner de GitHub.", env, ruta)
    anota(f"subida a Discord: {'OK' if ok else 'FALLÓ'} en {time.time() - t0:.1f} s")
except Exception as e:
    anota(f"subida a Discord: EXCEPCIÓN {type(e).__name__} tras {time.time() - t0:.1f} s")

print("\n-- Telegram sin molestar: sendChatAction (typig) en vez de sendPhoto --", flush=True)
t0 = time.time()
try:
    import urllib.parse as up
    datos = up.urlencode({"chat_id": env.get("TELEGRAM_HOME_CHANNEL"), "action": "typing"}).encode()
    with urllib.request.urlopen(urllib.request.Request(
            f"https://api.telegram.org/bot{env.get('TELEGRAM_BOT_TOKEN')}/sendChatAction",
            data=datos), timeout=20) as r:
        ok = json.loads(r.read().decode()).get("ok", False)
    anota(f"API de Telegram (invisible): {'OK' if ok else 'FALLÓ'} en {time.time() - t0:.1f} s")
except Exception as e:
    anota(f"API de Telegram: EXCEPCIÓN {type(e).__name__} tras {time.time() - t0:.1f} s")

texto = ("🔬 **Sonda de entrega (imágenes) desde GitHub Actions**\n" +
         "\n".join("• " + l for l in lineas) +
         "\nℹ️ Si las subidas fallan o tardan decenas de segundos, GitHub no puede mandar las "
         "alertas con gráfico (el ciclo se cuelga y lo corta el tiempo límite).")
S.DISCORD_OVERRIDE = os.environ.get("DISCORD_CANAL", "")

# el resultado también se escribe DENTRO del repo: así se puede leer desde afuera (el log del runner
# necesita autenticación, el archivo commitado no)
try:
    with open(os.path.join(RAIZ, "sonda_resultado.txt"), "w") as fh:
        fh.write(time.strftime("%Y-%m-%d %H:%M:%S UTC\n", time.gmtime()))
        fh.write(f"runner: {os.environ.get('RUNNER_NAME', '?')} · {os.environ.get('RUNNER_OS', '?')}\n")
        fh.write(f"gráfico: {'existe, ' + str(os.path.getsize(ruta)) + ' bytes' if os.path.exists(ruta) else 'NO EXISTE'}\n\n")
        fh.write("\n".join(lineas) + "\n")
    print("  resultado escrito en sonda_resultado.txt", flush=True)
except Exception as e:
    print("  no se pudo escribir el resultado:", type(e).__name__, flush=True)

print("  resumen a Discord:", S.send_discord(texto, env, None), flush=True)
