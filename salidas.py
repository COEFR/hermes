#!/usr/bin/env python3
"""
¿El edge está en la SALIDA? Prueba 11 formas de salir sobre las mismas señales de short.

La entrada ya está agotada: 7 variantes de entrada, ninguna sobrevive fuera de muestra. Pero la
salida mueve el resultado tanto como la señal (medido en el paso 2) y hasta ahora solo habíamos
probado dos variantes tontas. Acá se prueban estilos reales de gestión, sobre las mismas señales
(divergencia bajista clásica + volumen ≥1.2×), en las 3 ventanas anuales.

Sirve si queda positiva en LAS TRES ventanas.
"""
import json
import os
import sys
import time

import backtest as B
import profit as P

HERE = os.path.dirname(os.path.abspath(__file__))
FEE = 0.001
DIA = 86_400_000
VOL_MULT = 1.2


def soporte_cercano(c, e, precio, lookback=250):
    """Pivote bajo más cercano por debajo del precio (el nivel donde un humano tomaría ganancia)."""
    serie = c[:-1][-lookback:] if len(c) > lookback else c[:-1]
    lows, _ = B.pivots(serie)
    vals = sorted({float(serie[i][3]) for i in lows if float(serie[i][3]) < precio}, reverse=True)
    return vals[0] if vals else None


def sim_fijo(c, e, a, tp, sl, mb):
    return B.simulate(c, e, "short", a, mb, tp, sl)


def sim_soporte(c, e, a, mb, sl_mult=1.0):
    """Objetivo en el soporte real; si no hay soporte, objetivo 2xATR."""
    entry = float(c[e][4])
    sop = soporte_cercano(c, e, entry)
    tp_dist = (entry - sop) / a if sop else 2.0
    tp_dist = max(0.5, min(tp_dist, 6.0))          # acotado: ni ridículo ni absurdo
    return B.simulate(c, e, "short", a, mb, tp_dist, sl_mult)


def sim_be(c, e, a, mb, tp=3.0, be_at=1.0):
    """Mueve el stop a break-even después de +be_at R."""
    entry = float(c[e][4])
    risk = a
    sl, tp_px = entry + risk, entry - tp * a
    armado = False
    end = min(len(c), e + 1 + mb)
    salida = None
    for j in range(e + 1, end):
        h, l = float(c[j][2]), float(c[j][3])
        if not armado and l <= entry - be_at * risk:
            sl, armado = entry, True
        if h >= sl:
            salida = sl
            break
        if l <= tp_px:
            salida = tp_px
            break
    if salida is None:
        salida = float(c[end - 1][4])
    gross = (entry - salida) / entry
    rp = risk / entry
    return gross / rp, end - 1 - e, 100 * gross, rp


def sim_parcial(c, e, a, mb, tp1=1.5, tp2=3.0, sl_mult=1.0):
    """50% en 1.5xATR y 50% en 3xATR, con el stop movido a break-even tras el primer parcial."""
    entry = float(c[e][4])
    risk = sl_mult * a
    sl, p1, p2 = entry + risk, entry - tp1 * a, entry - tp2 * a
    mitad, abierta = None, True
    end = min(len(c), e + 1 + mb)
    r_total = None
    for j in range(e + 1, end):
        h, l = float(c[j][2]), float(c[j][3])
        if mitad is None and l <= p1:
            mitad = 0.5 * ((entry - p1) / entry - 2 * FEE / (risk / entry))
            sl = entry                              # break-even en el resto
        toca_stop = h >= sl
        toca_final = l <= p2
        if toca_stop and mitad is not None:
            r_total = mitad + 0.5 * ((entry - sl) / entry - 2 * FEE / (risk / entry))
            break
        if toca_stop and mitad is None:
            r_total = ((entry - sl) / entry - 2 * FEE / (risk / entry))
            break
        if toca_final:
            r_total = (mitad or 0.0) + (0.5 if mitad else 1.0) * (
                (entry - p2) / entry - 2 * FEE / (risk / entry))
            break
    if r_total is None:
        r_total = ((mitad or 0.0) + (0.5 if mitad else 1.0) *
                   ((entry - float(c[end - 1][4])) / entry - 2 * FEE / (risk / entry)))
    rp = risk / entry
    return r_total / 1.0, end - 1 - e, 100 * r_total * rp, rp


def sim_rsi(c, e, a, mb, rsi, umbral=50.0, sl_mult=1.0):
    """Salida cuando el RSI (de la temporalidad) vuelve a cruzar 50 hacia arriba."""
    entry = float(c[e][4])
    sl = entry + sl_mult * a
    end = min(len(c), e + 1 + mb)
    salida = None
    for j in range(e + 1, end):
        h, l = float(c[j][2]), float(c[j][3])
        if h >= sl:
            salida = sl
            break
        if rsi[j] is not None and rsi[j] > umbral:
            salida = float(c[j][4])
            break
    if salida is None:
        salida = float(c[end - 1][4])
    gross = (entry - salida) / entry
    rp = sl_mult * a / entry
    return gross / rp, end - 1 - e, 100 * gross, rp


def sim_tiempo(c, e, a, mb, barras=20, sl_mult=1.0):
    """Salida por tiempo: cierra a mercado a las N velas si no tocó el stop."""
    entry = float(c[e][4])
    sl = entry + sl_mult * a
    end = min(len(c), e + 1 + min(barras, mb))
    salida = None
    for j in range(e + 1, end):
        if float(c[j][2]) >= sl:
            salida = sl
            break
    if salida is None:
        salida = float(c[end - 1][4])
    gross = (entry - salida) / entry
    rp = sl_mult * a / entry
    return gross / rp, end - 1 - e, 100 * gross, rp


def main():
    prev = json.load(open(os.path.join(HERE, "backtest_results.json")))
    syms = prev["meta"]["symbols"]
    now = int(time.time() * 1000)
    hi_glob = (now // DIA) * DIA
    lo_glob = hi_glob - 1095 * DIA
    warm = {iv: max(300, 60) * B.MS[iv] for iv in ("15m", "1h", "4h", "1d")}
    warm["1d"] = 400 * B.MS["1d"]

    estilos = {
        "fijo 1.5xATR / 1xATR": lambda c, e, a, mb, r: sim_fijo(c, e, a, 1.5, 1.0, mb),
        "fijo 2xATR / 1xATR (actual)": lambda c, e, a, mb, r: sim_fijo(c, e, a, 2.0, 1.0, mb),
        "fijo 3xATR / 1xATR": lambda c, e, a, mb, r: sim_fijo(c, e, a, 3.0, 1.0, mb),
        "fijo 2xATR / 1.5xATR": lambda c, e, a, mb, r: sim_fijo(c, e, a, 2.0, 1.5, mb),
        "objetivo en el SOPORTE / 1xATR": lambda c, e, a, mb, r: sim_soporte(c, e, a, mb, 1.0),
        "objetivo en el SOPORTE / 1.5xATR": lambda c, e, a, mb, r: sim_soporte(c, e, a, mb, 1.5),
        "break-even tras +1R, objetivo 3xATR": lambda c, e, a, mb, r: sim_be(c, e, a, mb, 3.0, 1.0),
        "parcial 1.5R + resto 3R con BE": lambda c, e, a, mb, r: sim_parcial(c, e, a, mb),
        "salir cuando el RSI cruza 50": lambda c, e, a, mb, r: sim_rsi(c, e, a, mb, r),
        "salir por tiempo (20 velas)": lambda c, e, a, mb, r: sim_tiempo(c, e, a, mb, 20),
        "trailing 2xATR (sin objetivo)": None,      # se mide en addons.py, acá queda de referencia
    }

    filas = {k: [] for k in estilos}
    for sym in syms:
        data = {iv: B.klines(sym, iv, lo_glob - warm[iv], hi_glob) for iv in ("15m", "1h", "4h", "1d")}
        for iv in ("15m", "1h", "4h", "1d"):
            c = data[iv]
            if len(c) < 400:
                continue
            closes = [float(x[4]) for x in c]
            vols = [float(x[5]) for x in c]
            rsi, atr, v_sma = B.rsi_wilder(closes), B.atr_wilder(c), B.sma(vols, 20)
            lo_p, hi_p = B.pivots(c)
            mb = B.MAX_BARS[iv]
            for i in range(1, len(hi_p)):
                p1, p2 = hi_p[i - 1], hi_p[i]
                if p2 - p1 > B.PIVOT_LOOKBACK or p2 - p1 < B.PIVOT_K * 2:
                    continue
                e = p2 + B.PIVOT_K
                if not (lo_glob <= int(c[e][0]) < hi_glob) or atr[e] is None or v_sma[e] is None:
                    continue
                if (vols[e] / v_sma[e]) < VOL_MULT or rsi[p1] is None or rsi[p2] is None:
                    continue
                v1, v2 = float(c[p1][2]), float(c[p2][2])
                if not (v2 > v1 and (v2 - v1) / v1 >= B.MIN_PRICE_DIFF
                        and rsi[p2] < rsi[p1] - B.MIN_RSI_DIFF):
                    continue
                for nombre, fn in estilos.items():
                    if fn is None:
                        continue
                    r = fn(c, e, atr[e], mb, rsi)
                    if r is None:
                        continue
                    filas[nombre].append({"t": int(c[e][0]), "sym": sym, "tf": iv,
                                          "R0": r[0], "risk_pct": r[3]})
        print(f"  {sym} listo", file=sys.stderr)

    print("\n=== ¿EL EDGE ESTÁ EN LA SALIDA? (mismas 916 señales de short, 3 ventanas anuales) ===")
    print("# celda = % de movimiento por alerta (n); sirve solo si es positiva en LAS TRES ventanas")
    hdr = (f"{'estilo de salida':<36} {'n':>5} " + " ".join(f"{'ventana ' + str(i+1):>17}" for i in range(3))
           + f" {'¿3/3?':>7}")
    print(hdr)
    print("-" * len(hdr))
    resumen = {}
    for nombre, rows in filas.items():
        if len(rows) < 20:
            continue
        celdas, positivas, validas = [], 0, 0
        for w in range(3):
            w0 = lo_glob + w * 365 * DIA
            w1 = lo_glob + (w + 1) * 365 * DIA if w < 2 else hi_glob
            sel = sorted([r for r in rows if w0 <= r["t"] < w1], key=lambda r: r["t"])
            ult, keep = {}, []
            for r in sel:
                if r["t"] - ult.get(r["sym"], 0) < B.COOLDOWN_H * 3600_000:
                    continue
                ult[r["sym"]] = r["t"]
                keep.append(r)
            m = P.stats(keep, FEE, 12.0)
            if not m or m["n"] < 8:
                celdas.append("muestra corta".rjust(17))
                continue
            validas += 1
            positivas += 1 if m["mov_por_señal_pct"] > 0 else 0
            celdas.append(f"{m['mov_por_señal_pct']:+6.2f}% n={m['n']:<3}".rjust(17))
            resumen.setdefault(nombre, {})[f"v{w+1}"] = {
                "n": m["n"], "winrate": m["winrate"], "mov": m["mov_por_señal_pct"]}
        ok = validas == 3 and positivas == 3
        resumen.setdefault(nombre, {})["positiva_en_3"] = ok
        print(f"{nombre:<36} {len(rows):>5} " + " ".join(celdas) + f" {('SÍ' if ok else 'no'):>7}")

    json.dump(resumen, open(os.path.join(HERE, "salidas_results.json"), "w"), indent=1)
    print(f"\n# detalle -> {os.path.join(HERE, 'salidas_results.json')}")


if __name__ == "__main__":
    main()
