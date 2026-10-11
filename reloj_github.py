#!/usr/bin/env python3
"""RELOJ: dispara el workflow del bot en GitHub cada 15 minutos (y nada más).

Este script NO escanea ni envía señales: solo le dice a GitHub "corré ahora", que es lo que
GitHub sabe hacer bien. El escaneo, los gráficos y los envíos pasan a los servidores de GitHub
(gratis e ilimitados en repo público); acá solo queda el reloj.

Por qué hace falta: el programador interno de GitHub dispara cada 3-7 horas (medido), pero un
disparo por API crea la corrida al instante (medido: HTTP 204 en 1,8 s y 0 s de espera de máquina).

Si el disparo falla 3 veces seguidas, avisa por Discord (la Bot API directa, como las alertas).
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN = os.path.join(HERE, ".token_github")
LOG = os.path.join(HERE, "logs", "reloj.log")
ESTADO = os.path.join(HERE, ".reloj_estado.json")
REPO = "COEFR/hermes"
WORKFLOW = "bot.yml"


def log(msg):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    linea = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(linea, flush=True)
    with open(LOG, "a") as fh:
        fh.write(linea + "\n")


def avisar_discord(texto):
    """Aviso directo por la Bot API (sin pasar por Hermes), reusando el envío de las alertas."""
    try:
        sys.path.insert(0, HERE)
        import signals as S
        S.SEND_TELEGRAM = False
        return S.send_discord(texto, S.env_file(), None)
    except Exception as e:
        log(f"!! no pude avisar por Discord: {type(e).__name__}")
        return False


def tarea_segun_hora():
    """Qué hay que disparar ahora: el ciclo normal, el reporte diario o el marcador semanal.

    GitHub tiene su propio programador para eso, pero va con horas de atraso (medido 3-7 h), así que
    el reloj también se encarga: el reporte diario sale a las 12:00 UTC y el marcador los lunes 13:00.
    """
    ahora = time.gmtime()
    if ahora.tm_hour == 12 and ahora.tm_min < 12:
        return "0 12 * * *", "reporte diario"
    if ahora.tm_wday == 0 and ahora.tm_hour == 13 and ahora.tm_min < 12:
        return "0 13 * * 1", "marcador semanal"
    return "", "ciclo normal"


def main():
    if not os.path.exists(TOKEN):
        log("!! falta .token_github: no puedo disparar el workflow")
        return 1
    tok = open(TOKEN).read().strip()
    url = f"https://api.github.com/repos/{REPO}/actions/workflows/{WORKFLOW}/dispatches"
    tarea, nombre_tarea = tarea_segun_hora()
    payload = {"ref": "main"}
    if tarea:
        payload["inputs"] = {"tarea": tarea}
    cuerpo = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=cuerpo, method="POST", headers={
        "Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json",
        "Content-Type": "application/json", "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "hermes-reloj/1"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    except Exception as e:
        code = 0
        log(f"disparo falló: {type(e).__name__}")
    dt_ms = (time.time() - t0) * 1000

    estado = {}
    if os.path.exists(ESTADO):
        try:
            estado = json.load(open(ESTADO))
        except Exception:
            estado = {}
    if code == 204:
        log(f"disparo OK (HTTP 204) en {dt_ms:.0f} ms · {nombre_tarea} · corrida creada en GitHub")
        estado["fallos"] = 0
    else:
        estado["fallos"] = estado.get("fallos", 0) + 1
        log(f"disparo FALLÓ (HTTP {code}) · fallos seguidos: {estado['fallos']}")
        if estado["fallos"] == 3:
            avisar_discord(
                "⚠️ **El reloj del bot no puede disparar GitHub** (3 fallos seguidos).\n"
                f"Último código: HTTP {code}. Puede ser el token vencido "
                "(se renueva en GitHub → Settings → Developer settings → Fine-grained tokens).\n"
                "ℹ️ Mientras tanto, GitHub sigue con su propio programador: las señales llegan, "
                "pero cada 3-7 horas en vez de cada 15 minutos.")
    try:
        json.dump(estado, open(ESTADO, "w"), indent=1)
    except Exception:
        pass
    return 0 if code == 204 else 1


if __name__ == "__main__":
    sys.exit(main())
