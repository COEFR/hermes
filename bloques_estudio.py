#!/usr/bin/env python3
"""¿El bloque de orden (order block) mejora las señales de short? Se mide, no se opina.

Mismas reglas que el resto del proyecto: señales de divergencia bajista con volumen ≥1.2× (la puerta
del bot), salida 2×ATR/1×ATR, comisión 0.10%/lado, empate = stop, 15 pares, ~3 años, y el efecto
tiene que aguantar las 3 ventanas de 1 año.

Se prueban cuatro formas de usar el bloque:
  1. el precio está DENTRO de un bloque de oferta
  2. hay un bloque de oferta FRESCO por encima, a ≤3% (la zona donde vender un rebote)
  3. lo mismo pero a ≤1%
  4. hay un bloque de DEMANDA fresco por debajo a ≤3% (contexto de soporte)
"""
import glob
import json
import os
import statistics
import sys
import time

RAIZ = os.path.dirname(os.path.abspath(__file__))
if os.path.basename(RAIZ) == "dev":
    RAIZ = os.path.dirname(RAIZ)          # los scripts de dev/ viven un nivel abajo
sys.path.insert(0, RAIZ)
os.chdir(RAIZ)
import backtest as B
import orderblocks as OB

FEE = 0.001
TP, SL, VOL_MULT = 2.0, 1.0, 1.2
VENTANA = 365 * 86_400_000
CERCA = 0.03


def cargar(sym, iv):
    mejor = None
    for p in sorted(glob.glob(f"cache/{sym}_{iv}_*.json")):
        try:
            k = json.load(open(p))
        except Exception:
            continue
        if isinstance(k, list) and len(k) > 800 and (mejor is None or len(k) > len(mejor)):
            mejor = k
    return mejor


def señales(c, iv, sym):
    closes = [float(x[4]) for x in c]
    vols = [float(x[5]) for x in c]
    rsi = B.rsi_wilder(closes)
    atr = B.atr_wilder(c)
    v_sma = B.sma(vols, 20)
    lo_p, hi_p = B.pivots(c)
    salida = []
    cache_bloques = {}
    for i in range(1, len(hi_p)):
        p1, p2 = hi_p[i - 1], hi_p[i]
        if p2 - p1 > B.PIVOT_LOOKBACK or p2 - p1 < B.PIVOT_K * 2:
            continue
        e = p2 + B.PIVOT_K
        if e >= len(c) - 1 or rsi[p1] is None or rsi[p2] is None or atr[e] is None or not v_sma[e]:
            continue
        v1, v2 = float(c[p1][2]), float(c[p2][2])
        if not (v2 > v1 and (v2 - v1) / v1 >= B.MIN_PRICE_DIFF):
            continue
        if not (rsi[p2] < rsi[p1] - B.MIN_RSI_DIFF):
            continue
        vol_ratio = vols[e] / v_sma[e]
        if vol_ratio < VOL_MULT:
            continue
        r = B.simulate(c, e, "short", atr[e], B.MAX_BARS[iv], TP, SL)
        if r is None:
            continue
        R0, bars, _g, rp = r
        mov = (R0 - 2 * FEE / rp) * rp * 100
        entry = closes[e]
        # bloques de orden calculados SOLO con las velas hasta la entrada (sin lookahead)
        clave = e
        if clave not in cache_bloques:
            cache_bloques[clave] = OB.detectar(c[:e + 1], atr[:e + 1])
        arriba, abajo = OB.relevante(cache_bloques[clave], entry)
        dentro = bool(arriba and arriba["lo"] <= entry <= arriba["hi"])
        dist_arr = (arriba["lo"] / entry - 1) if arriba and not dentro else None
        dist_aba = (1 - abajo["hi"] / entry) if abajo else None
        salida.append({
            "sym": sym, "tf": iv, "t": int(c[e][0]), "mov": mov,
            "en_oferta": dentro,
            "oferta_fresca_3": bool(arriba and not dentro and arriba["estado"] == "fresco"
                                    and dist_arr is not None and dist_arr <= CERCA),
            "oferta_fresca_1": bool(arriba and not dentro and arriba["estado"] == "fresco"
                                    and dist_arr is not None and dist_arr <= 0.01),
            "demanda_cerca": bool(abajo and abajo["estado"] == "fresco"
                                  and dist_aba is not None and dist_aba <= CERCA),
        })
    return salida


def bloque(nombre, filas, filtro):
    sub = [f for f in filas if filtro(f)]
    if len(sub) < 25:
        print(f"  {nombre:<34} n={len(sub):>4}  (muestra chica)")
        return
    mov = statistics.fmean(f["mov"] for f in sub)
    t0 = min(f["t"] for f in filas)
    vent = []
    for w in range(3):
        vs = [f for f in sub if t0 + w * VENTANA <= f["t"] < t0 + (w + 1) * VENTANA]
        vent.append(statistics.fmean(f["mov"] for f in vs) if len(vs) >= 8 else None)
    todas_pos = all(v is not None and v > 0 for v in vent)
    marca = "  ← positivo en las 3" if todas_pos else ""
    print(f"  {nombre:<34} n={len(sub):>4} {mov:>+7.2f}%  ventanas "
          + " / ".join(f"{v:+.2f}%" if v is not None else "s/d" for v in vent) + marca)


def main():
    prev = json.load(open("backtest_results.json"))
    syms = prev["meta"]["symbols"]
    filas = []
    t0 = time.time()
    for sym in syms:
        for iv in ("4h", "1h"):
            c = cargar(sym, iv)
            if c:
                filas += señales(c, iv, sym)
    print(f"señales de short con volumen: {len(filas)} ({len(syms)} pares, 4h y 1h, "
          f"{time.time() - t0:.0f} s)")
    if len(filas) < 80:
        print("muestra insuficiente"); return
    print(f"\nreferencia (todas): {statistics.fmean(f['mov'] for f in filas):+.2f}% por señal\n")
    bloque("dentro de bloque de oferta", filas, lambda f: f["en_oferta"])
    bloque("NO dentro de bloque de oferta", filas, lambda f: not f["en_oferta"])
    bloque("oferta fresca ≤3% arriba", filas, lambda f: f["oferta_fresca_3"])
    bloque("oferta fresca ≤1% arriba", filas, lambda f: f["oferta_fresca_1"])
    bloque("sin oferta fresca cerca", filas, lambda f: not f["oferta_fresca_3"])
    bloque("demanda fresca ≤3% abajo", filas, lambda f: f["demanda_cerca"])
    bloque("sin demanda fresca cerca", filas, lambda f: not f["demanda_cerca"])
    json.dump(filas, open("bloques_results.json", "w"), indent=1)
    print("\nguardado en bloques_results.json")


main()
