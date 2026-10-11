#!/usr/bin/env python3
"""VIGILANTE del bot de señales: avisa si dejó de escanear.

Por qué existe: si el cron falla, el python se rompe o Binance deja de responder, el bot queda
mudo — y el silencio se lee como "no hay señales", que es el peor diagnóstico posible.

Qué mira (sin depender de Hermes: manda él mismo por la Bot API de Telegram):
  · que las líneas del bot sigan en el crontab
  · que el python del bot siga teniendo Pillow (los gráficos)
  · la hora del último "escaneo:" en los logs de cada flujo, contra el intervalo esperado
Anti-spam: un solo aviso cada 3 h (estado en `.watchdog_estado.json`) y aviso de "volvió" al recuperarse.

Uso: cron_watchdog.py [--dry-run] [--max-min N] [--simular-min N]
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import signals as S

ESTADO = os.path.join(HERE, ".watchdog_estado.json")
# (archivo de log, cada cuántos minutos debería escanear, tolerancia en minutos)
FLUJOS = [("logs/cron.log", 15, 25), ("logs/cron_radar.log", 15, 30),
          ("logs/cron_momentum.log", 30, 45), ("logs/cron_rebote.log", 5, 20)]
RE_PY = os.path.join(HERE, "venv", "bin", "python")


def ultimo_escaneo(log):
    """Timestamp (epoch) del último 'escaneo:' del log, o None."""
    ruta = os.path.join(HERE, log)
    if not os.path.exists(ruta):
        return None
    ultimo = None
    with open(ruta, errors="replace") as fh:
        for linea in fh:
            if "escaneo:" in linea:
                m = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", linea)
                if m:
                    ultimo = time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
    return ultimo


def revisar(max_min=None, simular=None):
    problemas = []
    ahora = time.time()

    cron = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    if "data/trading" not in cron:
        problemas.append("las líneas del bot NO están en el crontab")
    elif cron.count("data/trading") < 4:
        problemas.append(f"solo {cron.count('data/trading')} líneas del bot en el crontab (esperaba 4+)")

    if not os.path.exists(RE_PY):
        problemas.append("falta el python del bot (venv) → no hay gráficos")
    else:
        r = subprocess.run([RE_PY, "-c", "import PIL"], capture_output=True, text=True)
        if r.returncode != 0:
            problemas.append("el python del bot perdió Pillow → se cayeron los gráficos")

    for log, cada, tolerancia in FLUJOS:
        ts = ultimo_escaneo(log)
        if ts is None:
            problemas.append(f"{log}: no encuentro ningún escaneo")
            continue
        minutos = (ahora - ts) / 60
        if simular is not None:
            minutos = simular
        limite = max_min if max_min is not None else tolerancia
        if minutos > limite:
            problemas.append(f"{log}: último escaneo hace {minutos:.0f} min (esperaba cada {cada} min)")
    return problemas


def avisar(texto):
    env = S.env_file()
    token = env.get("TELEGRAM_BOT_TOKEN")
    chat = env.get("TELEGRAM_HOME_CHANNEL")
    if not token or not chat:
        print("sin Telegram configurado; imprimo el aviso:")
        print(texto)
        return False
    datos = urllib.parse.urlencode({"chat_id": chat, "text": texto, "parse_mode": "HTML",
                                    "disable_web_page_preview": "true"}).encode()
    try:
        with urllib.request.urlopen(
                urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=datos),
                timeout=25) as r:
            return bool(__import__("json").loads(r.read().decode()).get("ok"))
    except Exception as e:
        print("fallo el aviso por Telegram:", e)
        return False


def main():
    ap = argparse.ArgumentParser(description="Vigilante del bot de señales")
    ap.add_argument("--dry-run", action="store_true", help="no avisa, solo imprime")
    ap.add_argument("--max-min", type=float, default=None, help="forzar el límite de minutos")
    ap.add_argument("--simular-min", type=float, default=None,
                    help="prueba: simular que el último escaneo fue hace N minutos")
    args = ap.parse_args()
    # las líneas del log llevan hora: sin ella no se puede auditar la frescura de las corridas
    def log(msg):
        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}")

    problemas = revisar(args.max_min, args.simular_min)
    estado = {}
    if os.path.exists(ESTADO):
        try:
            estado = json.load(open(ESTADO))
        except Exception:
            estado = {}
    ultimo_aviso = estado.get("ultimo_aviso", 0)
    recuperado = estado.get("recuperado", True)

    if problemas:
        detalle = "\n".join("• " + p for p in problemas)
        texto = (f"⚠️ <b>Bot de señales: algo no está escaneando</b>\n{detalle}\n\n"
                 f"Reviso con: <code>tail -20 {HERE}/logs/cron.log</code>")
        log("PROBLEMAS:\n" + detalle)
        if args.dry_run:
            return 1
        if time.time() - ultimo_aviso > 3 * 3600:      # anti-spam: 1 aviso cada 3 h
            log(f"aviso enviado por Telegram: {avisar(texto)}")
            estado.update({"ultimo_aviso": int(time.time()), "recuperado": False,
                           "detalle": problemas[:4]})
            json.dump(estado, open(ESTADO, "w"), indent=1)
        else:
            log("problemas persisten; ya avisé hace poco (no repito)")
        return 1

    log("todo ok: los 4 flujos escanearon dentro del plazo")
    if not recuperado:
        avisar("✅ <b>Bot de señales: volvió a escanear</b>\nLos flujos están corriendo de nuevo.")
        log("recuperado: aviso enviado")
        estado.update({"recuperado": True, "detalle": []})
        json.dump(estado, open(ESTADO, "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
