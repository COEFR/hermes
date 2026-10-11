#!/usr/bin/env python3
"""Prueba end-to-end: toma una señal real, dibuja el gráfico y la manda a #hermes (solo Discord,
sin molestar el canal de señales) usando EXACTAMENTE las funciones que usa el bot en producción.
Después relee el canal por la API para confirmar que el adjunto llegó."""
import os, sys, time

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.chdir(RAIZ)
import signals as S

S.SEND_TELEGRAM = False                      # no molestar Telegram en la prueba
S.DISCORD_OVERRIDE = os.environ.get("DISCORD_CANAL", "")   # #hermes
S.FRESH_BARS = 200
S.INDIVIDUAL = True

print("constantes del bot:", {k: getattr(S, k) for k in ("CHART", "CHART_DIR", "CHART_KEEP",
                                                         "SEND_TELEGRAM", "DISCORD_OVERRIDE")})

s = None
for sym in S.top_symbols(20):
    for tf in ("15m", "1h", "4h"):
        try:
            ks = S.klines(sym, tf, 300)
        except Exception:
            continue
        for cand in S.find_signals(sym, tf, ks):
            cand["_ks"] = ks
            if s is None or cand["entry_ts"] > s["entry_ts"]:
                s = cand
        time.sleep(0.05)
if not s:
    raise SystemExit("no encontré ninguna señal real para la prueba")

print(f"señal elegida: {s['sym']} {s['tf']} · puntaje {s.get('score')} · entrada {s['entry']}")
img = S.grafico(s)
print("gráfico:", img, os.path.getsize(img) / 1024 if img else 0, "KB")

texto = ("🧪 <b>EJEMPLO del formato nuevo</b> (así se van a ver las señales reales, con gráfico)\n\n"
         + S.fmt_signal(s, S.measured_stats()))
res = S.deliver(texto, img)
print("envío:", res)

# --- releer el canal por la API para confirmar el adjunto
import json, urllib.request
env = S.env_file()
T = env["DISCORD_BOT_TOKEN"]
req = urllib.request.Request("https://discord.com/api/v10/channels/1551315228934017178/messages?limit=2",
                             headers={"Authorization": f"Bot {T}", "User-Agent": "hermes-check/1"})
with urllib.request.urlopen(req, timeout=25) as r:
    msgs = json.loads(r.read().decode())
m = msgs[0]
print("último mensaje:", m["author"]["username"], "·", m["timestamp"][:16], "·", len(m["content"]), "chars")
print("adjuntos:", [(a["filename"], a["size"], a.get("content_type"), a.get("width"), a.get("height"))
                    for a in m.get("attachments", [])])
if m.get("attachments"):
    url = m["attachments"][0]["url"]
    # el CDN de Discord devuelve 403 si no se manda User-Agent (era un falso positivo del test)
    req_png = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (hermes-check)"})
    with urllib.request.urlopen(req_png, timeout=25) as r:
        datos = r.read()
    firma = b"\x89PNG\r\n\x1a\n"
    print(f"descargado desde Discord: {len(datos)/1024:.0f} KB · es PNG: {datos[:8] == firma}")
