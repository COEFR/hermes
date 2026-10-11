#!/usr/bin/env python3
"""VIGILANTE DEL SILENCIO — avisa por Telegram si el bot dejó de dispararse.

El bot ya no corre en esta máquina: corre en GitHub Actions y lo dispara un reloj externo
(cron-job.org). Si ese reloj se cae, o GitHub deja de aceptar las corridas, el ÚNICO síntoma es
el silencio — y el silencio es indistinguible de «no hay señales». Este script mira la API de
GitHub y avisa al chat de Telegram con la Bot API directa (cero tokens de LLM).

Qué vigila:
  - antigüedad de la última corrida (tope por defecto 45 min = 3 ciclos de 15),
  - que las corridas no estén fallando seguidas,
  - que el disparo NO venga del programador de GitHub (si viene, el reloj externo está caído),
  - y avisa también cuando se recupera.

Anti-spam: avisa una vez y no repite hasta que pasen AVISO_CADA minutos (por defecto 120).

Uso: vigilante_silencio.py [--seco] [--tope MINUTOS] [--probar-aviso]
     --seco          no manda nada (para probar la detección)
     --tope N        minutos de silencio antes de avisar (default 45)
     --probar-aviso  manda un «escribiendo…» invisible a Telegram para probar token y chat
Sale siempre con código 0: el resultado va por Telegram, no por el exit code (para no llenar el
log del cron de errores).
"""
import argparse
import calendar
import json
import os
import time
import urllib.parse
import urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__))
REPO = "COEFR/Hermes-Signal-bot"
WORKFLOW = "bot.yml"
TOPE = 45
AVISO_CADA = 120          # minutos entre avisos mientras el problema siga
FALLAS_SEGUIDAS = 3       # corridas fallidas seguidas que disparan aviso
ESTADO = os.path.join(AQUI, ".vigilante_estado.json")
TOKEN_GH = os.path.join(AQUI, ".token_github")
ENV = os.path.expanduser("~/.hermes/.env")


def ahora():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def log(msg):
    print(f"{ahora()} {msg}", flush=True)


def leer_env(ruta=ENV):
    d = {}
    try:
        for linea in open(ruta, errors="replace"):
            linea = linea.strip()
            if linea and not linea.startswith("#") and "=" in linea:
                k, v = linea.split("=", 1)
                d[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return d


def api_gh(url):
    tok = open(TOKEN_GH).read().strip() if os.path.exists(TOKEN_GH) else os.environ.get("GITHUB_TOKEN", "")
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + tok,
                                               "Accept": "application/vnd.github+json",
                                               "User-Agent": "hermes-vigilante/1"})
    return json.load(urllib.request.urlopen(req, timeout=25))


def a_epoch(iso):
    return calendar.timegm(time.strptime(iso, "%Y-%m-%dT%H:%M:%SZ"))


def telegram(texto, invisible=False):
    env = leer_env()
    tok = env.get("TELEGRAM_BOT_TOKEN", "")
    chat = env.get("TELEGRAM_HOME_CHANNEL", "")
    if not tok or not chat:
        log("sin TELEGRAM_BOT_TOKEN o TELEGRAM_HOME_CHANNEL: no se puede avisar")
        return False
    metodo = "sendChatAction" if invisible else "sendMessage"
    datos = {"chat_id": chat, "action": "typing"} if invisible else \
            {"chat_id": chat, "text": texto, "disable_web_page_preview": "true"}
    try:
        with urllib.request.urlopen(
                f"https://api.telegram.org/bot{tok}/{metodo}",
                data=urllib.parse.urlencode(datos).encode(), timeout=20) as r:
            ok = r.status == 200
        log(f"aviso {'invisible ' if invisible else ''}a Telegram: {'ok' if ok else 'FALLÓ'}")
        return ok
    except Exception as e:
        log(f"no pude avisar por Telegram: {type(e).__name__}: {e}")
        return False


def cargar_estado():
    try:
        return json.load(open(ESTADO))
    except Exception:
        return {}


def guardar_estado(d):
    try:
        json.dump(d, open(ESTADO, "w"), indent=1)
    except Exception as e:
        log(f"no pude guardar el estado: {type(e).__name__}: {e}")


def discord(texto):
    """Manda el informe al canal de Discord (los informes van ahí, por pedido de Dylan)."""
    env = leer_env()
    tok = env.get("DISCORD_BOT_TOKEN", "")
    canal = env.get("DISCORD_HOME_CHANNEL", "")
    if not tok or not canal:
        log("sin DISCORD_BOT_TOKEN o DISCORD_HOME_CHANNEL: pruebo por Telegram")
        return telegram(texto)
    datos = json.dumps({"content": texto[:1990]}).encode()
    req = urllib.request.Request(f"https://discord.com/api/v10/channels/{canal}/messages", data=datos,
                                 headers={"Authorization": "Bot " + tok, "Content-Type": "application/json",
                                          "User-Agent": "hermes-vigilante/1"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            ok = r.status == 200
        log(f"parte enviado a Discord: {'ok' if ok else 'FALLÓ'}")
        return ok
    except Exception as e:
        log(f"no pude mandar el parte a Discord ({type(e).__name__}: {e}); pruebo por Telegram")
        return telegram(texto)


def parte_diario(seco=False):
    """Parte de las últimas 24 h para el chat de Telegram (cero tokens de LLM).

    Se manda una vez por día, después del reporte diario del bot: sirve para ver de un vistazo si
    el corredor y el reloj externo están cumpliendo, sin tener que preguntarle nada a nadie.
    """
    ahora_ts = time.time()
    lineas = [f"📋 PARTE DEL BOT · {time.strftime('%Y-%m-%d %H:%M')} (hora local)"]

    try:
        corridas = api_gh(f"https://api.github.com/repos/{REPO}/actions/workflows/{WORKFLOW}/runs?per_page=100")["workflow_runs"]
        recientes = [c for c in corridas if (ahora_ts - a_epoch(c["created_at"])) / 3600 <= 24]
        verdes = [c for c in recientes if c["conclusion"] == "success"]
        fallas = [c for c in recientes if c["conclusion"] == "failure"]
        cancel = [c for c in recientes if c["conclusion"] == "cancelled"]
        externos = [c for c in recientes if c["event"] == "workflow_dispatch"]
        marcas = sorted(a_epoch(c["created_at"]) for c in recientes)
        huecos = [f"{(b - a) / 60:.0f} min (hasta {time.strftime('%H:%M', time.localtime(b))})"
                  for a, b in zip(marcas, marcas[1:]) if (b - a) / 60 > 20]
        lineas.append(f"☁️ Ciclos en 24 h: {len(recientes)} de ~96 esperados · ✅ {len(verdes)} · ❌ {len(fallas)} · cancelados {len(cancel)}")
        lineas.append(f"⏰ Reloj externo: {len(externos)}/{len(recientes)} corridas")
        if huecos:
            lineas.append("🕳️ Huecos > 20 min: " + " · ".join(huecos[:5]))
        if fallas:
            lineas.append("❌ Fallaron: " + ", ".join(f"#{c['run_number']}" for c in fallas[:6]))
    except Exception as e:
        lineas.append(f"⚠️ no pude leer las corridas: {type(e).__name__}: {e}")

    def crudo(nombre):
        return urllib.request.urlopen(
            f"https://raw.githubusercontent.com/{REPO}/main/{nombre}", timeout=20).read().decode()

    try:
        tareas = json.loads(crudo("ultimas_tareas.json"))
        hoy = time.strftime("%Y-%m-%d")
        lineas.append(("✅ Reporte diario: enviado hoy" if tareas.get("reporte") == hoy
                       else "⚠️ Reporte diario: todavía no figura enviado hoy")
                      + f" · marcador: {tareas.get('marcador', '-')}")
    except Exception as e:
        lineas.append(f"⚠️ sin archivo de tareas todavía ({type(e).__name__})")

    try:
        s = json.loads([l for l in crudo("signals_log.jsonl").splitlines() if l.strip()][-1])
        lineas.append(f"📡 Última alerta: {s.get('sym')} {s.get('tf')} ({s.get('flujo')}) hace "
                      f"{(ahora_ts * 1000 - s.get('ts', 0)) / 3600000:.1f} h")
        cierres = [json.loads(l) for l in crudo("results.jsonl").splitlines() if l.strip()]
        nuevas = [c for c in cierres if (ahora_ts * 1000 - c.get("reportado_ts", 0)) / 3600000 <= 24]
        obj = sum(1 for c in nuevas if c.get("estado") == "objetivo")
        lineas.append(f"🏁 Cierres en 24 h: {len(nuevas)}" +
                      (f" (✅ {obj} al objetivo · ❌ {len(nuevas) - obj})" if nuevas else ""))
    except Exception as e:
        lineas.append(f"⚠️ no pude leer el seguimiento: {type(e).__name__}")

    hb = os.path.expanduser("~/.hermes/state/gateway.heartbeat")
    if os.path.exists(hb):
        lineas.append(f"🖥️ VPS: gateway vivo (latido hace {time.time() - os.path.getmtime(hb):.0f} s) · "
                      "solo los 2 vigilantes, nada del bot")
    texto = "\n".join(lineas)
    print(texto, flush=True)
    if not seco:
        discord(texto)
    return 0


def main():
    ap = argparse.ArgumentParser(description="Vigilante del silencio del bot de señales")
    ap.add_argument("--seco", action="store_true", help="no manda nada")
    ap.add_argument("--tope", type=float, default=TOPE, help="minutos de silencio antes de avisar")
    ap.add_argument("--probar-aviso", action="store_true", help="prueba token y chat (manda un «escribiendo…»)")
    ap.add_argument("--parte", action="store_true", help="manda el parte de las últimas 24 h")
    cli = ap.parse_args()

    if cli.parte:
        return parte_diario(cli.seco)

    if cli.probar_aviso:
        telegram("", invisible=True)
        return 0

    estado = cargar_estado()
    ahora_ts = time.time()
    problema = None

    try:
        corridas = api_gh(f"https://api.github.com/repos/{REPO}/actions/workflows/{WORKFLOW}/runs?per_page=10")["workflow_runs"]
    except Exception as e:
        corridas = []
        problema = f"no pude consultar la API de GitHub ({type(e).__name__}: {e})"

    if corridas and not problema:
        ult = corridas[0]
        edad = (ahora_ts - a_epoch(ult["created_at"])) / 60
        log(f"última corrida: #{ult['run_number']} hace {edad:.0f} min · {ult['status']}/{ult['conclusion']} · disparo: {ult['event']}")
        if edad > cli.tope:
            problema = (f"la última corrida fue hace {edad:.0f} min (tope {cli.tope:.0f}). "
                        f"O el reloj externo dejó de disparar, o GitHub no está aceptando las corridas.")
        else:
            seguidas = 0
            for c in corridas:
                if c["conclusion"] == "failure":
                    seguidas += 1
                elif c["conclusion"] in ("success",):
                    break
                # cancelled/pending en el medio no cortan la racha
                if c["status"] != "completed":
                    break
            if seguidas >= FALLAS_SEGUIDAS:
                problema = f"las últimas {seguidas} corridas terminaron en FALLA (el bot está corriendo pero rompiéndose)"
            elif ult["event"] != "workflow_dispatch":
                problema = ("la última corrida la disparó el programador de GitHub, no el reloj externo: "
                            "cron-job.org puede estar caído (GitHub solo dispara cada 1-9 h)")

    if problema:
        ultimo_aviso = estado.get("aviso_ts") or 0
        if (ahora_ts - ultimo_aviso) / 60 >= AVISO_CADA:
            texto = ("⚠️ BOT DE SEÑALES: posible caída\n" + problema +
                     f"\nCorridas: https://github.com/{REPO}/actions/workflows/{WORKFLOW}")
            log("PROBLEMA: " + problema)
            if not cli.seco and telegram(texto):
                estado["aviso_ts"] = ahora_ts
        else:
            log(f"problema ya avisado hace {(ahora_ts - ultimo_aviso) / 60:.0f} min: no repito")
    else:
        if estado.get("aviso_ts"):
            log("el bot volvió a disparar: aviso de recuperación")
            if not cli.seco:
                telegram("✅ BOT DE SEÑALES: volvió a disparar (el aviso de caída queda cancelado).")
            estado["aviso_ts"] = None
        else:
            log("todo bien: el reloj externo está disparando")

    estado["ultima_revision_ts"] = ahora_ts
    guardar_estado(estado)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
