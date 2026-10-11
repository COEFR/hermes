import os
#!/usr/bin/env python3
"""Prueba de entrega: manda un mensaje de prueba a Discord (#hermes) y a Telegram.

Se usa en la corrida MANUAL del workflow (botón "Run workflow"): si los secretos están bien
cargados, te llegan los dos mensajes y la corrida queda verde; si falta alguno, sale en rojo.
También sirve a mano:  python dev/prueba_entrega.py
"""
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.chdir(RAIZ)
import signals as S                                    # noqa: E402

TEXTO = ("🧪 **Prueba desde GitHub Actions**: las credenciales funcionan y el bot puede publicar. "
         "Si ves este mensaje, los 5 secretos están bien cargados.")
fallos = []

print(f"claves en el entorno: {sorted(k for k in S.env_file() if k.endswith(('TOKEN', 'CHANNEL')))}")

S.DISCORD_OVERRIDE = os.environ.get("DISCORD_CANAL", "")             # #hermes
S.SEND_TELEGRAM = False
res_d = S.deliver(TEXTO, None)
print("Discord:", res_d)
if not res_d.get("discord"):
    fallos.append("Discord")

S.SEND_TELEGRAM = True
ok_t = S.send_telegram(TEXTO)
print("Telegram:", ok_t)
if not ok_t:
    fallos.append("Telegram")

S.SEND_TELEGRAM = False                                # que no quede activado para el resto del ciclo

if fallos:
    print(f"\n❌ FALLÓ la entrega por: {', '.join(fallos)}")
    print("   Revisá los secretos del repo (Settings → Secrets and variables → Actions):")
    print("   TELEGRAM_BOT_TOKEN · TELEGRAM_HOME_CHANNEL · DISCORD_BOT_TOKEN · "
          "DISCORD_HOME_CHANNEL · DISCORD_SIGNALS_CHANNEL")
    sys.exit(1)
print("\n✅ entrega verificada en los dos canales")
