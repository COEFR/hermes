import os
#!/usr/bin/env python3
"""AUDITORÍA del sistema de señales en producción: cron, logs, scripts, gráficos, estado,
credenciales, entrega, gateway y recursos. Sale con código = nº de fallos.

Uso: salud.py [--enviar]   (--enviar manda un mensaje de prueba real a #hermes)
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import urllib.error
import calendar

REPO_GH = "COEFR/hermes"
TOPE_SILENCIO = 45      # minutos sin corridas = silencio (3 ciclos de 15)

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)
PY = os.path.join(HERE, "venv", "bin", "python")
fallos, avisos, oks = [], [], []


def ok(msg):
    oks.append(msg); print(f"  ✅ {msg}")


def mal(msg):
    fallos.append(msg); print(f"  ❌ {msg}")


def ojo(msg):
    avisos.append(msg); print(f"  ⚠️  {msg}")


ap = argparse.ArgumentParser(description="Auditoría del sistema de señales")
ap.add_argument("--datos", action="store_true",
                help="además verifica los datos crudos y las temporalidades contra Binance")
cli = ap.parse_args()   # ojo: 'args' se usa como variable de bucle más abajo

print(f"=== AUDITORÍA DEL SISTEMA DE SEÑALES · {time.strftime('%Y-%m-%d %H:%M')} ===")

# ---------------------------------------------------------------- 1. cron
print("\n[1] Cron")
cron = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
ESPERADAS = {
    "flujo premium": "signals.py --side short --min-adx 20",
    "flujo normales": "signals.py --side short --min-adx 0",
    "flujo momentum": "signals.py --tipo momentum",
    "flujo rebote": "signals.py --tipo rebote",
    "reporte diario": "signals.py --report",
    "tracker": "tracker.py",
    "resumen semanal": "tracker.py --resumen",
    "vigilante": "cron_watchdog.py",
    "contexto de mercado": "mercado.py",
    "vigilante del gateway": "gateway-watchdog.py",
}
LINEAS_BOT = [l for l in cron.splitlines()
              if any(s in l for s in ("signals.py", "tracker.py", "mercado.py", "gh_run.py", "reloj_github.py"))]
EN_GITHUB = not LINEAS_BOT                  # el bot se mudó: la VPS ya no lo corre
if EN_GITHUB:
    (ok if "gateway-watchdog.py" in cron else mal)("cron · vigilante del gateway de Hermes (esta VPS)")
    ok("cron · el bot ya no está en la VPS (0 líneas del bot): corre en GitHub")
    for nombre, marca in ESPERADAS.items():
        if nombre == "vigilante del gateway":
            continue
        if marca in cron:
            ojo(f"cron · {nombre}: quedó una línea en la VPS (¿doble emisor?)")
    (ok if "vigilante_silencio.py" in cron else ojo)("cron · vigilante del silencio (mira las corridas de GitHub)")
    (ok if "vigilante_silencio.py --parte" in cron else ojo)("cron · parte diario de las 24 h")
else:
    for nombre, marca in ESPERADAS.items():
        (ok if marca in cron else mal)(f"cron · {nombre}")
    if len(LINEAS_BOT) < 9:
        mal(f"solo {len(LINEAS_BOT)} líneas del bot en el cron (esperaba 9)")

for script in ("gateway-watchdog.py",):
    pass
print("  · cron del sistema:", subprocess.run(["systemctl", "is-enabled", "cron"],
      capture_output=True, text=True).stdout.strip() or "?")

# ---------------------------------------------------------------- 2. corredor y reloj
print("\n[2] Corredor (GitHub) y su reloj externo")
if EN_GITHUB:
    def gh(url):
        tok = open(".token_github").read().strip() if os.path.exists(".token_github") else ""
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + tok,
                                                   "Accept": "application/vnd.github+json",
                                                   "User-Agent": "hermes-salud/1"})
        return json.load(urllib.request.urlopen(req, timeout=25))

    def edad_utc(iso):
        return (time.time() - calendar.timegm(time.strptime(iso, "%Y-%m-%dT%H:%M:%SZ"))) / 60

    try:
        corridas = gh(f"https://api.github.com/repos/{REPO_GH}/actions/workflows/bot.yml/runs?per_page=10")["workflow_runs"]
    except Exception as e:
        corridas = []
        mal(f"no pude consultar las corridas de GitHub: {type(e).__name__}: {e}")
    if corridas:
        ult = corridas[0]
        edad = edad_utc(ult["created_at"])
        (ok if edad <= TOPE_SILENCIO else mal)(
            f"última corrida hace {edad:.0f} min · #{ult['run_number']} · {ult['conclusion']} · disparo: {ult['event']}")
        if ult["status"] == "completed":
            (ok if ult["conclusion"] == "success" else mal)(f"la última corrida terminó en {ult['conclusion']}")
        else:
            ok(f"la última corrida #{ult['run_number']} está {ult['status']} (sin conclusión todavía)")
        rotas = [c for c in corridas if c["conclusion"] in ("failure", "cancelled")]
        (ok if not rotas else ojo)(f"de las últimas {len(corridas)} corridas: {len(rotas)} fallidas/canceladas")
        if ult["event"] != "workflow_dispatch":
            ojo("la última corrida la disparó el programador de GitHub (va atrasado): revisá el reloj externo")
        try:
            commits = gh(f"https://api.github.com/repos/{REPO_GH}/commits?per_page=1")
            e2 = edad_utc(commits[0]["commit"]["author"]["date"])
            (ok if e2 <= TOPE_SILENCIO else mal)(f"último estado commiteado hace {e2:.0f} min")
        except Exception as e:
            ojo(f"no pude leer los commits: {type(e).__name__}")
    try:
        informe = urllib.request.urlopen(
            f"https://raw.githubusercontent.com/{REPO_GH}/main/ultimo_ciclo.txt", timeout=20).read().decode()
        e3 = edad_utc(informe.splitlines()[0].strip().replace(" UTC", "").replace(" ", "T") + "Z")
        (ok if e3 <= TOPE_SILENCIO else mal)(f"el informe del propio runner es de hace {e3:.0f} min")
        fallas = [l for l in informe.splitlines() if "FALLA" in l]
        (ok if not fallas else mal)(f"flujos con FALLA en ese informe: {len(fallas)}" +
                                    (f" → {fallas[0][:70]}" if fallas else ""))
    except Exception as e:
        ojo(f"no pude leer el informe del runner: {type(e).__name__}: {e}")
    ok("los logs locales de la VPS ya no se usan (el bot corre afuera): no se revisan")
else:
    PLAZOS = {"logs/cron.log": 40, "logs/cron_radar.log": 40, "logs/cron_momentum.log": 70,
              "logs/cron_rebote.log": 20, "logs/cron_tracker.log": 40,
              "logs/cron_watchdog.log": 45, "logs/cron_mercado.log": 40}
    for log, minutos in PLAZOS.items():
        if not os.path.exists(log):
            mal(f"{log} no existe"); continue
        txt = open(log, errors="replace").read()[-20000:]
        marcas = re.findall(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", txt)
        if not marcas:
            mal(f"{log}: sin entradas con hora"); continue
        edad = (time.time() - time.mktime(time.strptime(marcas[-1], "%Y-%m-%d %H:%M:%S"))) / 60
        (ok if edad <= minutos else mal)(f"{log}: última actividad hace {edad:.0f} min (tope {minutos})")
        errores = [l for l in txt.splitlines()[-200:] if l.startswith("!!") or "Traceback" in l]
        if errores:
            mal(f"{log}: {len(errores)} error(es) recientes → {errores[-1][:90]}")
        else:
            ok(f"{log}: sin errores en las últimas 200 líneas")

# ---------------------------------------------------------------- 3. scripts
print("\n[3] Scripts (compilación e importación)")
for f in sorted(glob.glob("*.py")):
    r = subprocess.run([PY, "-c", f"import py_compile,sys; py_compile.compile('{f}', doraise=True)"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        mal(f"{f} no compila: {r.stderr.strip().splitlines()[-1] if r.stderr else '?'}")
r = subprocess.run([PY, "-c", "import signals, chart, tracker, scoring, mercado, cron_watchdog"],
                   capture_output=True, text=True)
(ok if r.returncode == 0 else mal)("los módulos importan juntos" + ("" if r.returncode == 0 else f": {r.stderr[-200:]}"))

# ---------------------------------------------------------------- 4. escaneo en seco
print("\n[4] Escaneo en seco (no debe enviar)")


def envios():
    return sum(1 for l in open("logs/signals.log", errors="replace") if "enviado ok=" in l)


antes = envios()
for args in ("--side short --min-adx 20 --intervals 15m,1h,4h,1d --label premium",
             "--tipo momentum --intervals 1d --label momentum",
             "--tipo rebote --intervals 1h --label rebote"):
    r = subprocess.run([PY, "signals.py"] + args.split() + ["--individual", "--min-score", "0",
                       "--dry-run", "--no-state"], capture_output=True, text=True, timeout=240)
    salida = r.stdout + r.stderr
    if re.search(r"sin alertas|DIVERGENCIA|MÁXIMO DE 30|REBOTE TRAS", salida):
        ok(f"flujo [{args.split('--label')[1].strip()}] escanea bien")
    else:
        mal(f"flujo [{args.split('--label')[1].strip()}] salida inesperada: {salida.strip()[-120:]}")
despues = envios()
(ok if antes == despues else mal)(f"el dry-run no envió nada (envíos {antes} → {despues})")

# ---------------------------------------------------------------- 5. gráficos
print("\n[5] Gráficos")
import signals as S
import chart as CH
import tracker as TR
try:
    ks = S.klines("BTCUSDT", "4h", 300)
    S.FRESH_BARS = 400
    sig = S.find_signals("BTCUSDT", "4h", ks)
    if sig:
        ruta = CH.render(sig[0], ks, "charts/_salud_alerta.png")
        ok(f"gráfico de alerta ({os.path.getsize(ruta) // 1024} KB)")
    else:
        ojo("sin divergencia en BTC 4h para probar el gráfico de alerta")
    filas = TR.leer_jsonl("results.jsonl")
    if filas:
        ruta = TR.grafico_resultado(filas[-1], S.klines(filas[-1]["sym"], filas[-1]["tf"], 300), filas[-1])
        (ok if ruta else mal)(f"gráfico de cierre ({os.path.getsize(ruta) // 1024} KB)" if ruta else "gráfico de cierre falló")
        ruta = CH.render_marcador(filas, "charts/_salud_marcador.png")
        ok(f"marcador visual ({os.path.getsize(ruta) // 1024} KB)")
    else:
        ojo("sin cierres para probar el marcador")
except Exception as e:
    mal(f"gráficos: {type(e).__name__}: {e}")

# ---------------------------------------------------------------- 6. estado
print("\n[6] Estado y consistencia")
for f in ("state.json", "state_premium.json", "state_normales.json", "state_momentum.json",
          "state_rebote.json", "watchlist_extra.json", "score_calib.json", "shorts_results.json"):
    if not os.path.exists(f):
        ojo(f"{f} no existe (puede ser normal si el flujo nunca corrió)")
        continue
    try:
        json.load(open(f))
        ok(f"{f} es JSON válido")
    except Exception as e:
        mal(f"{f} corrupto: {e}")
señales = TR.leer_jsonl("signals_log.jsonl")
resultados = TR.leer_jsonl("results.jsonl")
ok(f"señales registradas: {len(señales)} · cierres: {len(resultados)}")
claves = {s.get("clave") for s in resultados}
huerfanos = [r for r in resultados if r.get("clave") not in
             {f"{s.get('sym')}|{s.get('tf')}|{s.get('pivot_ts')}" for s in señales}]
(ok if not huerfanos else mal)(f"cierres sin señal de origen: {len(huerfanos)}")
sin_resolver = [s for s in señales
                if f"{s.get('sym')}|{s.get('tf')}|{s.get('pivot_ts')}" not in claves]
ok(f"señales abiertas (esperando cierre): {len(sin_resolver)}")
dupes = len(resultados) - len({r.get("clave") for r in resultados})
(ok if dupes == 0 else mal)(f"cierres duplicados: {dupes}")

# ---------------------------------------------------------------- 7. credenciales y entrega
print("\n[7] Credenciales y destinos")
env = S.env_file()


def api(url, headers=None):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers or
                                      {"User-Agent": "hermes-salud/1"}), timeout=20) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception as e:
        return 0, str(e)


st, _ = api(f"https://api.telegram.org/bot{env.get('TELEGRAM_BOT_TOKEN')}/getMe")
(ok if st == 200 else mal)(f"token de Telegram (HTTP {st})")
st, _ = api("https://discord.com/api/v10/users/@me",
            {"Authorization": f"Bot {env.get('DISCORD_BOT_TOKEN')}", "User-Agent": "hermes-salud/1"})
(ok if st == 200 else mal)(f"token de Discord (HTTP {st})")
for nombre, cid in (("#hermes", os.environ.get("DISCORD_CANAL", "")),
                    ("#resultados-señales", "1551671378065752255"),
                    ("#señales-rsi-premium", env.get("DISCORD_SIGNALS_CHANNEL"))):
    if not cid:
        mal(f"{nombre}: sin id configurado"); continue
    st, _ = api(f"https://discord.com/api/v10/channels/{cid}",
                {"Authorization": f"Bot {env.get('DISCORD_BOT_TOKEN')}", "User-Agent": "hermes-salud/1"})
    (ok if st == 200 else mal)(f"canal {nombre} accesible (HTTP {st})")
ultimo = subprocess.run(["tail", "-1", "logs/signals.log"], capture_output=True, text=True).stdout.strip()
if EN_GITHUB:
    ojo(f"los logs locales son históricos (la VPS ya no envía): {ultimo[:70]}")
    filas = TR.leer_jsonl("signals_log.jsonl")
    if filas:
        u = filas[-1]
        horas = (time.time() * 1000 - u.get("ts", 0)) / 3600000
        ok(f"última alerta del repo: {u.get('sym')} {u.get('tf')} ({u.get('flujo')}) hace {horas:.1f} h")
else:
    ok(f"último envío registrado: {ultimo[:80]}")

# ---------------------------------------------------------------- 8. gateway y recursos
print("\n[8] Gateway y recursos")
r = subprocess.run(["systemctl", "--user", "is-active", "hermes-gateway"], capture_output=True, text=True)
(ok if r.stdout.strip() == "active" else mal)(f"servicio hermes-gateway: {r.stdout.strip()}")
hb = os.path.expanduser("~/.hermes/state/gateway.heartbeat")
if os.path.exists(hb):
    edad = time.time() - os.path.getmtime(hb)
    (ok if edad < 300 else ojo)(f"latido del gateway hace {edad:.0f} s")
else:
    ojo("sin archivo de latido del gateway")
libre = subprocess.run(["df", "-h", "/"], capture_output=True, text=True).stdout.splitlines()[-1].split()
ok(f"disco: {libre[3]} libres de {libre[1]} ({libre[4]} usado)")
mem = subprocess.run(["free", "-m"], capture_output=True, text=True).stdout.splitlines()[1].split()
(ok if int(mem[6]) > 200 else ojo)(f"RAM disponible: {mem[6]} MB")
carga = os.getloadavg()[0]
(ok if carga < 2 else ojo)(f"carga media: {carga:.2f}")

# ---------------------------------------------------------------- 9. paquete de migración
print("\n[9] Kit de migración")
r = subprocess.run(["bash", "migracion/empaquetar.sh", "/tmp/_salud_bot.tar.gz"],
                   capture_output=True, text=True)
if r.returncode == 0:
    tam = os.path.getsize("/tmp/_salud_bot.tar.gz") // 1024
    ok(f"se puede empaquetar ({tam} KB)")
    os.remove("/tmp/_salud_bot.tar.gz")
else:
    mal(f"empaquetar falla: {r.stderr[-150:]}")
kit = glob.glob(os.path.join(os.path.expanduser("~"), ".hermes", "cache", "scratch",
                             "bot_senales.tar.gz"))
if kit:
    edad_kit = (time.time() - os.path.getmtime(kit[0])) / 3600
    if edad_kit > 2:
        ojo(f"el paquete guardado tiene {edad_kit:.1f} h: regeneralo antes de migrar (el código cambió)")

# ---------------------------------------------------------------- 10. datos y temporalidades
if cli.datos:
    print("\n[10] Verificación de datos y temporalidades")
    for script, clave in (("verifica_datos.py", "diferencias exactas: 0"),
                          ("verifica_temporalidad.py", "RESULTADO")):
        r = subprocess.run([PY, script], capture_output=True, text=True, timeout=600)
        lineas = [l.strip() for l in (r.stdout or "").splitlines() if clave in l]
        detalle = (lineas[0] if lineas else "sin la línea esperada") if script == "verifica_datos.py" \
            else (lineas[-1] if lineas else "sin resultado")
        (ok if r.returncode == 0 else mal)(f"{script} → {detalle}")

# ---------------------------------------------------------------- resumen
print(f"\n=== RESULTADO: {len(oks)} OK · {len(avisos)} avisos · {len(fallos)} FALLOS ===")
for f in fallos:
    print(f"  ❌ {f}")
for a in avisos:
    print(f"  ⚠️  {a}")
sys.exit(len(fallos))
