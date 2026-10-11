#!/usr/bin/env python3
"""Manda por Telegram (chat privado 1:1) los 5 valores que hay que cargar como Secrets en GitHub.
No imprime los valores: solo confirma qué se envió y con qué longitud."""
import json, os, sys, urllib.parse, urllib.request

sys.path.insert(0, "/home/ubuntu/.hermes/data/trading")
os.chdir("/home/ubuntu/.hermes/data/trading")
import signals as S

env = S.env_file()
CLAVES = ["TELEGRAM_BOT_TOKEN", "TELEGRAM_HOME_CHANNEL", "DISCORD_BOT_TOKEN",
          "DISCORD_HOME_CHANNEL", "DISCORD_SIGNALS_CHANNEL"]
faltan = [k for k in CLAVES if not env.get(k)]
if faltan:
    raise SystemExit(f"faltan claves en el .env: {faltan}")

CAB = ("🔑 <b>Valores para GitHub Secrets</b> (los 5 que faltan cargar)\n"
       "Van en: repo → <b>Settings → Secrets and variables → Actions → New repository secret</b>\n"
       "Uno por uno: el <b>Name</b> exacto (tal cual está) y el <b>Secret</b> de abajo.\n\n"
       "⚠️ Ojo con los espacios: copiá el valor completo, sin espacios al principio ni al final.\n")
lineas = [CAB]
for k in CLAVES:
    lineas.append(f"\n<b>{k}</b>\n<code>{env[k]}</code>")
lineas.append("\n\n✅ Cuando los tengas los 5 cargados, apretá <b>Run workflow</b> en la pestaña Actions "
              "y te tiene que llegar un mensaje de prueba acá y en Discord.\n"
              "ℹ️ Después de cargarlos, borrá este mensaje.")

texto = "\n".join(lineas)
token, chat = env["TELEGRAM_BOT_TOKEN"], env["TELEGRAM_HOME_CHANNEL"]
datos = urllib.parse.urlencode({"chat_id": chat, "text": texto, "parse_mode": "HTML",
                                "disable_web_page_preview": "true"}).encode()
with urllib.request.urlopen(urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage", data=datos), timeout=30) as r:
    res = json.loads(r.read().decode())
print("enviado ok:", res.get("ok"), "· message_id:", (res.get("result") or {}).get("message_id"))
for k in CLAVES:
    print(f"  {k:<26} {len(env[k])} caracteres (valor oculto, va por Telegram)")
