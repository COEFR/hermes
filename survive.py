#!/usr/bin/env python3
"""
¿Sobrevive ALGUNA combinación en las tres ventanas de 1 año?

La validación principal (oos.py) mostró que la config del bot pierde en las dos ventanas viejas y
solo gana en la que se usó para elegir los filtros: sobreajuste. Este script barre todas las celdas
(dirección × temporalidad × nivel) y reporta las que quedan positivas en LAS TRES ventanas.

Advertencia metodológica que el propio script imprime: con ~24 celdas, por azar algunas quedan
positivas en las tres; por eso exige muestra mínima y reporta n e IC de cada ventana.
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
MIN_ADX = 25.0
MIN_N_VENTANA = 12          # muestra mínima por ventana para mirar una celda
MIN_N_TOTAL = 45


def main():
    prev = json.load(open(os.path.join(HERE, "backtest_results.json")))
    syms = prev["meta"]["symbols"]
    now = int(time.time() * 1000)
    hi = (now // DIA) * DIA
    lo = hi - 1095 * DIA
    warm = {iv: max(300, 60) * B.MS[iv] for iv in ("15m", "1h", "4h", "1d")}
    warm["1d"] = 400 * B.MS["1d"]

    rows = []
    for sym in syms:
        data = {iv: B.klines(sym, iv, lo - warm[iv], hi) for iv in ("15m", "1h", "4h", "1d")}
        for iv in ("15m", "1h", "4h", "1d"):
            c = data[iv]
            if len(c) < 400:
                continue
            closes = [float(x[4]) for x in c]
            vols = [float(x[5]) for x in c]
            rsi = B.rsi_wilder(closes)
            atr = B.atr_wilder(c)
            adx = B.adx_wilder(c)
            v_sma = B.sma(vols, 20)
            for s in B.build_signals(c, rsi, atr):
                e = s["entry_idx"]
                if not (lo <= int(c[e][0]) < hi) or atr[e] is None or v_sma[e] is None:
                    continue
                vr = vols[e] / v_sma[e] if v_sma[e] else 0
                if vr < VOL_MULT:
                    continue
                a = adx[e] if e < len(adx) else None
                r = B.simulate(c, e, s["dir"], s["atr"], B.MAX_BARS[iv], 2.0, 1.0)
                if r is None:
                    continue
                tier = "premium" if (a and a[0] >= MIN_ADX) else "normal"
                rows.append({"tf": iv, "tier": tier, "dir": s["dir"], "t": int(c[e][0]),
                             "sym": sym, "R0": r[0], "risk_pct": r[3]})
        print(f"  {sym}", file=sys.stderr)

    ventanas = []
    for w in range(3):
        w0 = lo + w * 365 * DIA
        w1 = lo + (w + 1) * 365 * DIA if w < 2 else hi
        ventanas.append((time.strftime("%Y-%m", time.gmtime(w0 / 1000)),
                         time.strftime("%Y-%m", time.gmtime(w1 / 1000)), w0, w1))

    def con_cooldown(sub):
        sub = sorted(sub, key=lambda r: r["t"])
        ult, keep = {}, []
        for r in sub:
            if r["t"] - ult.get(r["sym"], 0) < B.COOLDOWN_H * 3600_000:
                continue
            ult[r["sym"]] = r["t"]
            keep.append(r)
        return keep

    celdas = {}
    for r in rows:
        celdas.setdefault((r["dir"], r["tf"], r["tier"]), []).append(r)

    print("\n=== ¿QUÉ SOBREVIVE EN LAS TRES VENTANAS? (volumen ≥1.2×, TP 2×ATR / SL 1×ATR) ===")
    print("# una celda solo cuenta si tiene muestra suficiente en cada ventana; por azar algunas pasan")
    hdr = (f"{'celda':<26} {'n tot':>6} " + " ".join(f"{'% ' + v[0]:>16}" for v in ventanas) + f" {'¿3/3?':>7}")
    print(hdr)
    print("-" * len(hdr))
    supervivientes = []
    for (d, tf, tier), sub in sorted(celdas.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2])):
        if len(sub) < MIN_N_TOTAL:
            continue
        partes, ok, valido = [], True, True
        for nombre, _, w0, w1 in ventanas:
            keep = con_cooldown([r for r in sub if w0 <= r["t"] < w1])
            m = P.stats(keep, FEE, 12.0)
            if not m or m["n"] < MIN_N_VENTANA:
                valido = False
                partes.append("muestra corta".rjust(16))
                continue
            partes.append(f"{m['mov_por_señal_pct']:+6.2f}% n={m['n']:<3}".rjust(16))
            if m["mov_por_señal_pct"] <= 0:
                ok = False
        etiqueta = f"{'LONG' if d == 'long' else 'SHORT'} {tf} {tier}"
        print(f"{etiqueta:<26} {len(sub):>6} " + " ".join(partes) + f" {('SÍ' if (ok and valido) else 'no'):>7}")
        if ok and valido:
            supervivientes.append(etiqueta)

    print(f"\nceldas positivas en las tres ventanas: {len(supervivientes)} de "
          f"{sum(1 for k, v in celdas.items() if len(v) >= MIN_N_TOTAL)} evaluadas"
          + (f" → {', '.join(supervivientes)}" if supervivientes else " → NINGUNA"))


if __name__ == "__main__":
    main()
