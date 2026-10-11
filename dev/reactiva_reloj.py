#!/usr/bin/env python3
"""Reactiva (o revisa) el reloj temporal en la VPS y dispara un ciclo de inmediato."""
import os
import subprocess
import sys

DIR = "/home/ubuntu/.hermes/data/trading"
PY = f"{DIR}/venv/bin/python"
LINEA = f"*/15 * * * * cd {DIR} && {PY} reloj_github.py >> logs/cron_reloj.log 2>/dev/null"

cron = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
if "reloj_github" not in cron:
    nuevo = cron.rstrip("\n") + "\n" + LINEA + "\n"
    subprocess.run(["crontab", "-"], input=nuevo, text=True, check=True)
    print("reloj de la VPS REACTIVADO (respaldo temporal) ✓")
else:
    print("el reloj de la VPS ya estaba activo")

print("\ncron de la VPS ahora:")
for l in subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout.splitlines():
    print("  " + l)

print("\ndisparo inmediato para no perder más ciclos:")
r = subprocess.run([PY, os.path.join(DIR, "reloj_github.py")], capture_output=True, text=True)
print("  " + (r.stdout or r.stderr).strip())
