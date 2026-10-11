#!/usr/bin/env python3
"""Robustez de los dos cortes que aguantaron las 3 ventanas, y aviso de lookahead.

IMPORTANTE: el corte «BTC bajó en la misma ventana» NO es utilizable en vivo — usa el movimiento de
BTC DESPUÉS de la señal (lookahead). Sirve como explicación (atribución), no como filtro.
Los dos cortes que sí se conocen al momento de alertar son: día de la semana y sesión horaria.
Se revisan con: mitades temporales, por par, y combinados.
"""
import json
import statistics

filas = json.load(open("candidatas2_results.json"))
t0 = min(f["t"] for f in filas)
MITAD = 1.5 * 365 * 86_400_000


def bloque(nombre, sub):
    if len(sub) < 20:
        print(f"  {nombre:<34} n={len(sub):>4}  (muestra chica)")
        return
    mov = statistics.fmean(f["mov"] for f in sub)
    m1 = [f for f in sub if f["t"] < t0 + MITAD]
    m2 = [f for f in sub if f["t"] >= t0 + MITAD]
    v1 = statistics.fmean(f["mov"] for f in m1) if len(m1) >= 10 else None
    v2 = statistics.fmean(f["mov"] for f in m2) if len(m2) >= 10 else None
    pares = {}
    for f in sub:
        pares.setdefault(f["sym"], []).append(f["mov"])
    ganan = sum(1 for v in pares.values() if len(v) >= 3 and statistics.fmean(v) > 0)
    con_pares = sum(1 for v in pares.values() if len(v) >= 3)
    marca = "  ← positivo en las 2 mitades" if (v1 or 0) > 0 and (v2 or 0) > 0 else ""
    print(f"  {nombre:<34} n={len(sub):>4} {mov:>+7.2f}%  mitades "
          f"{v1:+.2f}%/{v2:+.2f}%  pares positivos {ganan}/{con_pares}{marca}")


print("=== control de robustez (mitades + por par) ===")
bloque("todas las señales (referencia)", filas)
print()
print("  -- por sesión horaria --")
for etq, f in (("Asia (0-7 UTC)", lambda x: x["hora"] < 8),
               ("Europa (8-15 UTC)", lambda x: 8 <= x["hora"] < 16),
               ("América (16-23 UTC)", lambda x: x["hora"] >= 16)):
    bloque(etq, [x for x in filas if f(x)])
print()
print("  -- por día --")
bloque("Fin de semana", [x for x in filas if x["dia"] in (5, 6)])
bloque("Laborable", [x for x in filas if x["dia"] not in (5, 6)])
print()
print("  -- combinaciones --")
bloque("Fin de semana + Asia/Europa", [x for x in filas if x["dia"] in (5, 6) and x["hora"] < 16])
bloque("Laborable + Asia/Europa", [x for x in filas if x["dia"] not in (5, 6) and x["hora"] < 16])
bloque("Laborable + América (peor caso)", [x for x in filas if x["dia"] not in (5, 6) and x["hora"] >= 16])
bloque("Fin de semana + América", [x for x in filas if x["dia"] in (5, 6) and x["hora"] >= 16])
print()
print("=== cuántas alertas se perderían con cada corte ===")
n = len(filas)
for etq, f in (("quitar sesión América", lambda x: x["hora"] < 16),
               ("quitar laborables", lambda x: x["dia"] in (5, 6)),
               ("quitar laborables Y América", lambda x: x["dia"] in (5, 6) or x["hora"] < 16)):
    keep = [x for x in filas if f(x)]
    mov = statistics.fmean(x["mov"] for x in keep) if keep else 0
    print(f"  {etq:<30} quedan {len(keep):>4}/{n} ({100*len(keep)/n:.0f}%) · {mov:+.2f}% por señal")
