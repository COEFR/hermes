#!/usr/bin/env python3
"""SONDA DE RED desde el runner de GitHub: mide latencia y errores al pedir datos a Binance y al
avisar por Discord/Telegram. Es la prueba definitiva de si GitHub puede correr el bot (que hace
~400 pedidos por ciclo) o si Binance limita las IPs de los servidores de CI.

Solo stdlib. Manda el resultado a #hermes por la Bot API (si puede) y lo imprime.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.chdir(RAIZ)

ENV = {}
for ruta in (os.path.expanduser("~/.hermes/.env"), os.path.join(RAIZ, ".env")):
    if os.path.exists(ruta):
        for l in open(ruta):
            l = l.strip()
            if l and not l.startswith("#") and "=" in l:
                k, v = l.split("=", 1)
                ENV[k.strip()] = v.strip().strip('"').strip("'")

lineas = []


def medir(nombre, url, headers=None, veces=10):
    tiempos, errores = [], []
    for i in range(veces):
        t0 = time.time()
        try:
            req = urllib.request.Request(url, headers=headers or {"User-Agent": "hermes-sonda/1"})
            with urllib.request.urlopen(req, timeout=30) as r:
                r.read()
                codigo = r.status
        except urllib.error.HTTPError as e:
            codigo = e.code
            errores.append(e.code)
        except Exception as e:
            codigo = 0
            errores.append(type(e).__name__)
        tiempos.append(time.time() - t0)
    media = sum(tiempos) / len(tiempos)
    linea = (f"{nombre}: {media * 1000:.0f} ms de media ({min(tiempos) * 1000:.0f}-{max(tiempos) * 1000:.0f} ms) "
             f"· errores: {len(errores)}/{veces} {sorted(set(errores)) if errores else ''}")
    print("  " + linea, flush=True)
    lineas.append(linea)
    return media


print("=== SONDA DE RED desde el runner de GitHub ===", flush=True)
medir("Binance ping", "https://api.binance.com/api/v3/ping", veces=5)
medir("Binance velas (1h, 300)", "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1h&limit=300")
medir("Binance velas (15m, 300)", "https://api.binance.com/api/v3/klines?symbol=ETHUSDT&interval=15m&limit=300")
medir("Binance futuros ping", "https://fapi.binance.com/fapi/v1/ping", veces=5)
medir("Discord API", "https://discord.com/api/v10/users/@me",
      headers={"Authorization": f"Bot {ENV.get('DISCORD_BOT_TOKEN', '')}", "User-Agent": "hermes-sonda/1"},
      veces=3)
medir("Telegram API", f"https://api.telegram.org/bot{ENV.get('TELEGRAM_BOT_TOKEN', '')}/getMe", veces=3)

# estimación: el ciclo del bot hace ~400 pedidos
try:
    media_velas = float([l for l in lineas if "Binance velas (1h" in l][0].split(":")[1].split("ms")[0]) / 1000
except Exception:
    media_velas = 0
if media_velas:
    estimado = 400 * media_velas / 60
    resumen = (f"⏱️ estimación del ciclo completo (≈400 pedidos a Binance): **{estimado:.1f} min** "
               f"· en la VPS tarda ~2 min")
    print("  " + resumen, flush=True)
    lineas.append(resumen.replace("**", ""))

texto = ("🔬 **Sonda de red desde GitHub Actions**\n" + "\n".join("• " + l for l in lineas) +
         "\nℹ️ Si la latencia a Binance es alta o hay errores, GitHub no puede correr el bot "
         "(Binance limita las IPs compartidas de los runners).")

token, chat = ENV.get("TELEGRAM_BOT_TOKEN"), ENV.get("TELEGRAM_HOME_CHANNEL")
try:
    import urllib.parse
    datos = urllib.parse.urlencode({"chat_id": chat, "text": texto}).encode()
    with urllib.request.urlopen(urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage", data=datos), timeout=30) as r:
        print("  aviso por Telegram:", json.loads(r.read().decode()).get("ok"), flush=True)
except Exception as e:
    print("  no se pudo avisar por Telegram:", type(e).__name__, flush=True)
