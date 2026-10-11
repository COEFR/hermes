#!/usr/bin/env python3
"""Lanzador para GitHub Actions.

Corre los flujos que corresponden al horario que disparó el job. En GitHub no hay `cron` propio ni
procesos que queden vivos: cada corrida levanta la máquina, ejecuta, guarda el estado en un commit y
se apaga. Por eso todo el sistema se reduce a **un workflow cada 15 minutos** que llama a este script.

Qué hace en cada horario (lo decide la cron que disparó, que llega en la variable `SCHEDULE`):

  */15 * * * *   premium, normales, momentum, rebote, contexto de mercado y seguimiento (tracker)
  0 12 * * *     reporte diario
  0 13 * * 1     marcador semanal (con el gráfico)

Notas:
- El flujo de **rebote pasa de cada 5 min a cada 15** (GitHub no baja de 5 min y encima puede
  atrasarse): es el precio de no tener VPS.
- `GH_DRY=1` corre todo en seco (sin enviar), para probar el workflow sin spamear.
"""

import json
import os
import subprocess
import sys
import time

AQUI = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
# Sub-canales por CALIDAD (pedido de Dylan): el puntaje 1-10 de cada señal decide dónde va, porque
# las de cantidad son muchas más que las buenas.
#   puntaje ≥8 → premium (calidad) · 5-7 → normales (medias) · <5 → cantidad (el resto)
CANAL_ALTO = "1551315228258607144"      # señales-rsi-premium
CANAL_MEDIO = "1551322667888681030"     # señales-rsi-normales
CANAL_BAJO = "1558296711099711501"      # señales-rsi-cantidad (canal nuevo)
CANAL_NORMALES = "1551322667888681030"
CANAL_MOMENTUM = "1551343421082443867"
CANAL_REBOTE = "1551343421896342049" if False else "1551343421896261754"
SECO = os.environ.get("GH_DRY") == "1"


def args_secos(args):
    """En seco: --dry-run en todos, y --no-state solo donde existe (los flujos de señales).
    Mercado y tracker no aceptan --no-state: pasarlo sería un error de argumentos."""
    if not SECO:
        return []
    return ["--dry-run", "--no-state"] if args[0] == "signals.py" else ["--dry-run"]

FLUJOS = {
    "premium": ["signals.py", "--side", "short", "--min-adx", "20", "--intervals", "15m,30m,1h,4h,1d",
                "--label", "premium", "--min-score", "0", "--individual",
                "--canal-alto", CANAL_ALTO, "--canal-medio", CANAL_MEDIO, "--canal-bajo", CANAL_BAJO],
    "normales": ["signals.py", "--side", "short", "--min-adx", "0", "--no-vol", "--intervals",
                 "15m,30m,1h,4h,1d", "--label", "normales", "--discord-channel", CANAL_NORMALES,
                 "--no-telegram", "--min-score", "0", "--individual",
                 "--canal-alto", CANAL_ALTO, "--canal-medio", CANAL_MEDIO, "--canal-bajo", CANAL_BAJO],
    "momentum": ["signals.py", "--tipo", "momentum", "--intervals", "1d", "--label", "momentum",
                 "--discord-channel", CANAL_MOMENTUM, "--no-telegram", "--individual",
                 "--min-score", "0"],
    "rebote": ["signals.py", "--tipo", "rebote", "--intervals", "1h", "--label", "rebote",
               "--discord-channel", CANAL_REBOTE, "--no-telegram", "--individual", "--min-score", "0"],
    "contexto de mercado": ["mercado.py"],
    "seguimiento": ["tracker.py"],
    "reporte diario": ["signals.py", "--report"],
    "marcador semanal": ["tracker.py", "--resumen", "--dias", "7", "--telegram"],
}

PLAN = {
    "*/15 * * * *": ["premium", "normales", "momentum", "rebote", "contexto de mercado", "seguimiento"],
    "0 12 * * *": ["reporte diario"],
    "0 13 * * 1": ["marcador semanal"],
}


TAREAS = None      # se define en main (ruta del archivo de tareas cumplidas)


def tareas_del_dia(hora=None, dia=None, ruta=None):
    """Flujos que salen UNA vez por día aunque el programador de GitHub no colabore.

    El reporte diario se manda en el primer ciclo que corra desde las 16:00 UTC (= 12:00 en Bolivia,
    que es la hora a la que venía llegando) y el marcador semanal en el primero desde las 17:00 UTC
    de los lunes (= 13:00 en Bolivia). Los umbrales están en UTC porque el runner y el reloj externo
    trabajan en UTC: si se quiere otra hora local, se cambia este número.
    Se anota la fecha en un archivo del repo (`ultimas_tareas.json`), así no se repite aunque el
    reloj externo dispare a otra hora.
    """
    gt = time.gmtime()
    hora = gt.tm_hour if hora is None else hora
    dia = gt.tm_wday if dia is None else dia
    hoy = time.strftime("%Y-%m-%d", gt)
    ruta = ruta or TAREAS
    estado = {}
    if ruta and os.path.exists(ruta):
        try:
            estado = json.load(open(ruta))
        except Exception:
            estado = {}
    extra = []
    if hora >= 16 and estado.get("reporte") != hoy:
        extra.append("reporte diario")
        estado["reporte"] = hoy
    if dia == 0 and hora >= 17 and estado.get("marcador") != hoy:
        extra.append("marcador semanal")
        estado["marcador"] = hoy
    if extra and ruta:
        try:
            json.dump(estado, open(ruta, "w"), indent=1)
        except Exception:
            pass
    return extra


def escribir_informe(horario, resultados):
    """Deja el informe del ciclo en `ultimo_ciclo.txt` (el workflow lo commitea).

    Motivo: el log de GitHub Actions necesita autenticación para leerse, así que el bot deja su
    propio informe dentro del repo — sirve para diagnosticar y para saber si el ciclo hizo algo."""
    try:
        ruta = os.path.join(AQUI, "ultimo_ciclo.txt")
        with open(ruta, "w") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}\n")
            fh.write(f"horario disparado: {horario or '(manual)'} · seco: {SECO}\n")
            fh.write(f"runner: {os.environ.get('RUNNER_NAME', 'local')} · python {sys.version.split()[0]}\n\n")
            for nombre, ok, dur, salida in resultados:
                fh.write(f"[{'OK ' if ok else 'FALLA'}] {nombre}: {dur:.0f} s\n")
                for l in (salida or "").splitlines()[-4:]:
                    fh.write(f"      {l}\n")
        print(f"  informe del ciclo escrito en {ruta}", flush=True)
    except Exception as e:
        print(f"  no se pudo escribir el informe: {type(e).__name__}: {e}", flush=True)


def correr(nombre, args):
    t0 = time.time()
    cmd = [PY] + args + args_secos(args)
    print(f"\n=== {nombre} ===\n$ {' '.join(cmd)}", flush=True)
    r = subprocess.run(cmd, cwd=AQUI, capture_output=True, text=True, timeout=900)
    salida = (r.stdout or "") + (r.stderr or "")
    for linea in salida.splitlines()[-6:]:
        print("   " + linea, flush=True)
    dur = time.time() - t0
    if r.returncode != 0:
        print(f"   ❌ {nombre} terminó con código {r.returncode} ({dur:.0f} s)", flush=True)
        return False, dur, salida
    print(f"   ✅ {nombre} OK ({dur:.0f} s)", flush=True)
    return True, dur, salida


def main():
    horario = os.environ.get("SCHEDULE", "").strip()
    plan = PLAN.get(horario) or PLAN["*/15 * * * *"]
    # OJO CON EL ORDEN: TAREAS tiene que estar definido ANTES de llamar a tareas_del_dia(), porque ahí
    # se usa para escribir el archivo que anota "ya se mandó hoy". Estaba definido DESPUÉS (abajo), así
    # que `ruta` quedaba en None, el estado no se escribía nunca y el REPORTE DIARIO salía en cada ciclo
    # —se midió: 98 veces en vez de 1—. Bug de orden, no de lógica.
    global TAREAS
    TAREAS = os.path.join(AQUI, "ultimas_tareas.json")
    extra = tareas_del_dia()
    if extra:
        # sin duplicar: el programador de GitHub puede disparar el MISMO horario que la lógica por
        # hora, y ahí el flujo salía dos veces en el mismo ciclo (reporte diario duplicado).
        plan = list(dict.fromkeys(list(plan) + extra))
        print(f"tareas del día que corresponden ahora: {', '.join(extra)}", flush=True)
    print(f"corrida de GitHub Actions · horario disparado: {horario or '(manual)'}")
    print(f"flujos a correr: {', '.join(plan)}" + ("  [EN SECO]" if SECO else ""))
    for carpeta in ("logs", "charts"):
        os.makedirs(os.path.join(AQUI, carpeta), exist_ok=True)
    resultados = []
    for nombre in plan:
        ok, dur, salida = correr(nombre, FLUJOS[nombre])
        resultados.append((nombre, ok, dur, salida))
    escribir_informe(horario, resultados)
    fallos = [n for n, ok, _d, _s in resultados if not ok]
    print("\n=== resumen ===")
    print(f"flujos OK: {len(plan) - len(fallos)}/{len(plan)}" + (f" · fallaron: {', '.join(fallos)}" if fallos else ""))
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
