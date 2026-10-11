import os
#!/usr/bin/env python3
"""PRUEBA DE CLON LIMPIO: emula lo que hará GitHub Actions.

Clona el repo en /tmp, crea el entorno desde cero (como el workflow: python + pillow), escribe el
`.env` como lo hace el workflow y corre el ciclo completo EN SECO. Verifica además que el ciclo en
seco no modifique ningún archivo de estado (eso haría commits basura en GitHub).

Uso: dev/prueba_clon_limpio.sh [ruta_del_repo]
"""
import os
import re
import shutil
import subprocess
import sys

REPO = sys.argv[1] if len(sys.argv) > 1 else "/home/ubuntu/.hermes/data/trading"
CLON = "/tmp/clon_limpio"
ENV_REAL = os.path.expanduser("~/.hermes/.env")
fallos = 0


def paso(titulo, cmd, cwd=None, env=None):
    global fallos
    r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=900)
    ok = r.returncode == 0
    print(("  ✅ " if ok else "  ❌ ") + titulo)
    if not ok:
        fallos += 1
        print("     " + (r.stdout or r.stderr or "").strip()[-300:].replace("\n", "\n     "))
    return r


print("=== PRUEBA DE CLON LIMPIO (lo que hará GitHub) ===")
shutil.rmtree(CLON, ignore_errors=True)

paso("git clone del repo tal como quedará en GitHub", ["git", "clone", "-q", REPO, CLON])

# el workflow escribe el .env desde los Secrets EN ~/.hermes/.env (fuera del repo): se emula igual
if os.path.exists(ENV_REAL):
    os.makedirs(os.path.expanduser("~/.hermes"), exist_ok=True)
    print("  (se usa el ~/.hermes/.env real, que es donde el workflow lo escribe)")

paso("crear el entorno e instalar Pillow (como el workflow)",
     ["bash", "-lc", "python3 -m venv venv && venv/bin/pip -q install 'pillow>=10,<13'"], cwd=CLON)
PY = os.path.join(CLON, "venv", "bin", "python")

print("\n  archivos clave presentes en el clon:")
for f in ("signals.py", "chart.py", "tracker.py", "orderblocks.py", "scoring.py", "gh_run.py",
          "mercado.py", ".github/workflows/bot.yml", "score_calib.json", "shorts_results.json",
          "signals_log.jsonl"):
    existe = os.path.exists(os.path.join(CLON, f))
    print(("  ✅ " if existe else "  ❌ ") + f)
    if not existe:
        fallos += 1

print("\n  ¿el clon trae secretos? (no debe)")
sospechosos = []
for raiz, _dirs, archivos in os.walk(CLON):
    if ".git" in raiz or "/venv/" in raiz:
        continue
    for a in archivos:
        p = os.path.join(raiz, a)
        try:
            txt = open(p, errors="ignore").read()
        except Exception:
            continue
        # se busca un VALOR real (≥20 chars) y no la mención de la variable: el workflow dice
        # TELEGRAM_BOT_TOKEN=${{ secrets.… }} y este mismo archivo contiene la cadena que busca
        if re.search(r"(TELEGRAM_BOT_TOKEN|DISCORD_BOT_TOKEN)\s*=\s*[A-Za-z0-9_\-:.]{20,}", txt) \
                and "${{" not in txt:
            sospechosos.append(p)
print(("  ✅ ninguno" if not sospechosos else "  ❌ " + str(sospechosos)))
if sospechosos:
    fallos += 1

print("\n  ciclo completo en seco, con el estado ANTES y DESPUÉS:")
antes = subprocess.run(["git", "status", "--porcelain"], cwd=CLON, capture_output=True, text=True).stdout
for horario in ("*/15 * * * *", "0 12 * * *", "0 13 * * 1"):
    r = subprocess.run([PY, "gh_run.py"], cwd=CLON, capture_output=True, text=True, timeout=900,
                       env={**os.environ, "GH_DRY": "1", "SCHEDULE": horario})
    ultima = [l for l in r.stdout.splitlines() if "flujos OK" in l]
    print(("  ✅ " if r.returncode == 0 else "  ❌ ") +
          f"horario '{horario}' → {ultima[0].strip() if ultima else 'sin resumen'}")
    if r.returncode != 0:
        fallos += 1
        print("     " + (r.stdout + r.stderr).strip()[-300:])

despues = subprocess.run(["git", "status", "--porcelain"], cwd=CLON, capture_output=True, text=True).stdout
if antes == despues:
    print("  ✅ el ciclo en seco NO tocó ningún archivo (nada de commits basura)")
else:
    print("  ❌ el ciclo en seco modificó archivos:\n" + despues[:400])
    fallos += 1

print("\n  prueba de ENVÍO real desde el clon (a #hermes):")
codigo = """
import sys; sys.path.insert(0, ".")
import signals as S
S.SEND_TELEGRAM = False
S.DISCORD_OVERRIDE = os.environ.get("DISCORD_CANAL", "")
print("envío desde clon:", S.deliver("🧪 Prueba del clon limpio: el bot funciona desde un checkout nuevo.", None))
"""
r = subprocess.run([PY, "-c", codigo], cwd=CLON, capture_output=True, text=True, timeout=120)
print(("  ✅ " if "discord': True" in r.stdout else "  ❌ ") + (r.stdout.strip() or r.stderr.strip())[-120:])
if "discord': True" not in r.stdout:
    fallos += 1

print(f"\n=== RESULTADO DEL CLON LIMPIO: {'todo correcto' if fallos == 0 else str(fallos) + ' fallo(s)'} ===")
sys.exit(1 if fallos else 0)
