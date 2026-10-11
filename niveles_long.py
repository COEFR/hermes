#!/usr/bin/env python3
"""¿Qué stop y objetivo tienen sentido para momentum (máximo de 30 días) y rebote (caída ≥5% en 1h)?

Los dos flujos mandan señales SIN niveles medidos, así que no se pueden seguir ni comparar. Acá se
miden estructuras de salida sobre 3 años y se reporta cuál (si alguna) aguanta las 3 ventanas.

- Momentum: 1d, cierre = máximo de los últimos 30 cierres (misma definición que find_momentum).
- Rebote: 1h, caída ≥5% en una vela con volumen ≥3× la media de 20 (igual que find_rebote).
- Entrada al cierre de la vela de la señal (sin lookahead). Largo: objetivo arriba, stop abajo.
- Empate objetivo/stop en la misma vela → STOP (conservador). Comisión 0.10%/lado (spot).
- Cooldown de 5 velas por par para no contar 20 veces el mismo episodio.
"""
import glob
import json
import os
import sys

RAIZ = os.path.dirname(os.path.abspath(__file__))
if os.path.basename(RAIZ) == "dev":
    RAIZ = os.path.dirname(RAIZ)          # los scripts de dev/ viven un nivel abajo
sys.path.insert(0, RAIZ)
import signals as S

CACHE = os.path.join(RAIZ, "cache")
FEE = 0.10          # % por lado
COOLDOWN = 5
ESTRUCTURAS = [(1.0, 2.0, 20), (1.5, 2.0, 20), (1.5, 3.0, 20), (2.0, 3.0, 20),
               (1.5, 6.0, 40), (1.5, 2.0, 50)]


def señales_momentum(k):
    cierres = [float(c[4]) for c in k]
    rsi = S.rsi_wilder(cierres)
    atr = S.atr_wilder(k)
    salida, ultimo = [], -999
    for i in range(35, len(k) - 1):
        if ultimo >= 0 and i - ultimo < COOLDOWN:
            continue
        ventana = cierres[max(0, i - 30):i + 1]
        if cierres[i] != max(ventana) or atr[i] is None:
            continue
        ultimo = i
        salida.append((i, atr[i]))
    return salida


def señales_rebote(k):
    vols = [float(c[5]) for c in k]
    cierres = [float(c[4]) for c in k]
    atr = S.atr_wilder(k)
    salida, ultimo = [], -999
    for i in range(25, len(k) - 1):
        if ultimo >= 0 and i - ultimo < COOLDOWN:
            continue
        if atr[i] is None or not cierres[i - 1]:
            continue
        vmed = sum(vols[i - 20:i]) / 20
        caida = 1 - cierres[i] / cierres[i - 1]
        if not vmed or caida < 0.05 or vols[i] < 3 * vmed:
            continue
        ultimo = i
        salida.append((i, atr[i]))
    return salida


def simular(k, i, atr, sl_mult, tp_mult, timeout, largo=True):
    """% de movimiento neto de comisiones, o None si no se resolvió."""
    entry = float(k[i][4])
    riesgo = sl_mult * atr
    premio = tp_mult * atr
    if largo:
        stop, objetivo = entry - riesgo, entry + premio
        for j in range(i + 1, min(i + 1 + timeout, len(k))):
            hi, lo = float(k[j][2]), float(k[j][3])
            if lo <= stop:
                return -riesgo / entry * 100 - 2 * FEE, j
            if hi >= objetivo:
                return premio / entry * 100 - 2 * FEE, j
    return (float(k[min(i + timeout, len(k) - 1)][4]) - entry) / entry * 100 - 2 * FEE, timeout


def medir(tipo, intervalo, patron, estructuras):
    archivos = sorted(glob.glob(os.path.join(CACHE, patrón := f"*_{intervalo}_{patron}_*.json")))
    filas = []
    for path in archivos:
        try:
            k = json.load(open(path))
        except Exception:
            continue
        if not isinstance(k, list) or len(k) < 500:
            continue
        sym = os.path.basename(path).split("_")[0]
        señales = señales_momentum(k) if tipo == "momentum" else señales_rebote(k)
        for i, atr in señales:
            for est in estructuras:
                r = simular(k, i, atr, est[0], est[1], est[2])
                if r:
                    filas.append({"sym": sym, "ts": int(k[i][0]), "est": est, "mov": r[0],
                                  "velas": r[1], "atr_pct": 100 * atr / float(k[i][4])})
    return filas


def reporte(titulo, filas, estructuras):
    print(f"\n=== {titulo} ===")
    if not filas:
        print("  sin señales"); return
    for est in estructuras:
        sub = [f for f in filas if f["est"] == est]
        if not sub:
            continue
        n = len(sub)
        mov = sum(f["mov"] for f in sub) / n
        gana = sum(1 for f in sub if f["mov"] > 0) / n * 100
        to = min(f["ts"] for f in sub)
        año = 365 * 86_400_000
        vent = []
        for w in range(3):
            vs = [f for f in sub if to + w * año <= f["ts"] < to + (w + 1) * año]
            vent.append(sum(f["mov"] for f in vs) / len(vs) if vs else None)
        ata = sum(f["atr_pct"] for f in sub) / n
        marca = "  <-- las 3 ventanas en positivo" if all(v is not None and v > 0 for v in vent) else ""
        print(f"  stop {est[0]}×ATR / objetivo {est[1]}×ATR / tope {est[2]} velas: "
              f"n={n} · {gana:.0f}% ganadoras · {mov:+.2f}% por señal · ATR medio {ata:.2f}% · "
              f"ventanas " + " / ".join(f"{v:+.2f}%" if v is not None else "s/d" for v in vent) + marca)


if __name__ == "__main__":
    m = medir("momentum", "1d", "1660694400000", ESTRUCTURAS)
    reporte("MOMENTUM (máximo de 30 días, diario · 20 pares · 3 años)", m, ESTRUCTURAS)
    r = medir("rebote", "1h", "1694174400000", ESTRUCTURAS)
    reporte("REBOTE (caída ≥5% en 1h con 3× volumen · 20 pares · 3 años)", r, ESTRUCTURAS)
    json.dump({"momentum": m, "rebote": r}, open("niveles_long_results.json", "w"), indent=1)
    print("\nguardado en niveles_long_results.json")
