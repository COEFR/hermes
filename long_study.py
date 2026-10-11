#!/usr/bin/env python3
"""
Calibra el puntaje 1-10 de los DOS tipos nuevos de señal (long momentum y rebote tras caída),
con el mismo método que se usó para las divergencias: deciles medidos sobre 3 años.

  momentum : el par hace nuevo máximo de 30 días (en velas diarias).
             Característica = subida de 30 días (%). Objetivo medido = retorno a 7 días.
  rebote   : caída ≥5% en 1 hora con volumen ≥3× la media de 20 (velas de 1h).
             Característica = magnitud de la caída (%). Objetivo medido = retorno a 2 horas.

Salida: long_calib.json (lo lee signals.py para puntuar cada alerta).
"""
import json
import os
import statistics as st
import sys
import time

import backtest as B
import profit as P

HERE = os.path.dirname(os.path.abspath(__file__))
DIA = 86_400_000
LOOKBACK_D = 30          # días para el máximo
HORIZONTE_D = 7          # días de horizonte del momentum
DROP = 0.05              # caída mínima
VOL_MULT = 3.0           # volumen mínimo relativo
HORIZONTE_H = 2          # horas de horizonte del rebote


def deciles(rows, clave, objetivo, etiqueta):
    """Corta por deciles de `clave` y mide el resultado de `objetivo` en cada uno."""
    vals = sorted(r[clave] for r in rows)
    n = len(vals)
    cortes = [vals[min(n - 1, int(round(q * n)))] for q in [i / 10 for i in range(1, 10)]]
    out = []
    for d in range(1, 11):
        lo = -1e18 if d == 1 else cortes[d - 2]
        hi = 1e18 if d == 10 else cortes[d - 1]
        grp = [r for r in rows if (r[clave] >= lo and r[clave] < hi) or (d == 10 and r[clave] >= lo)]
        if not grp:
            continue
        res = [r[objetivo] for r in grp]
        out.append({"score": d, "n": len(grp), "min": round(lo, 4), "max": round(hi, 4),
                    "retorno_pct": round(100 * st.mean(res), 2),
                    "mediana_pct": round(100 * st.median(res), 2),
                    "aciertos": round(100 * sum(1 for x in res if x > 0) / len(res), 1)})
    print(f"\n=== {etiqueta} ({n} eventos) ===")
    print(f"{'score':>5} {'n':>5} {'rango de ' + clave:>22} {'retorno':>9} {'mediana':>9} {'aciertos':>9}")
    for d in out:
        rango = f"{d['min']:.3g} a {d['max']:.3g}"
        print(f"{d['score']:>5} {d['n']:>5} {rango:>22} "
              f"{d['retorno_pct']:>8}% {d['mediana_pct']:>8}% {d['aciertos']:>8}%")
    return {"decile_cuts": [round(c, 4) for c in cortes], "deciles": out}


def main():
    prev = json.load(open(os.path.join(HERE, "backtest_results.json")))
    syms = prev["meta"]["symbols"]
    now = int(time.time() * 1000)
    hi = (now // DIA) * DIA
    lo = hi - 1095 * DIA
    warm = {iv: max(300, 60) * B.MS[iv] for iv in ("1h", "1d")}
    warm["1d"] = 400 * B.MS["1d"]

    ev_mom, ev_reb, base = [], [], []
    for sym in syms:
        d = B.klines(sym, "1d", lo - warm["1d"], hi)
        h = B.klines(sym, "1h", lo - warm["1h"], hi)
        cl = [float(k[4]) for k in d]
        ts = [int(k[0]) for k in d]
        for i in range(LOOKBACK_D, len(cl) - HORIZONTE_D - 1):
            if not (lo <= ts[i] < hi):
                continue
            f = cl[i + HORIZONTE_D] / cl[i] - 1
            base.append(f)
            if cl[i] == max(cl[i - LOOKBACK_D:i + 1]):
                ev_mom.append({"f": (cl[i] / cl[i - LOOKBACK_D] - 1) * 100, "t": f})
        ks = [k for k in h if lo <= int(k[0]) < hi]
        for i in range(25, len(ks) - HORIZONTE_H - 1):
            r1 = float(ks[i][4]) / float(ks[i - 1][4]) - 1
            vmed = st.mean(float(k[5]) for k in ks[i - 20:i])
            if vmed and r1 <= -DROP and float(ks[i][5]) >= VOL_MULT * vmed:
                ev_reb.append({"f": -r1 * 100, "t": float(ks[i + HORIZONTE_H][4]) / float(ks[i][4]) - 1})
        print(f"  {sym}", file=sys.stderr)

    print(f"=== BASELINE (deriva normal) ===")
    print(f"   momentum: retorno a {HORIZONTE_D} días = {100*st.mean(base):+.2f}% de media "
          f"(n={len(base)})")

    calib = {"meta": {"generated": time.strftime("%Y-%m-%d %H:%M:%S %z"), "days": 1095,
                      "symbols": syms, "lookback_dias": LOOKBACK_D, "horizonte_dias": HORIZONTE_D,
                      "drop_pct": DROP * 100, "vol_mult": VOL_MULT, "horizonte_horas": HORIZONTE_H,
                      "baseline_momentum_pct": round(100 * st.mean(base), 2)},
             "momentum": deciles([{"f": e["f"], "t": e["t"]} for e in ev_mom], "f", "t",
                                 f"MOMENTUM — nuevo máximo de {LOOKBACK_D} días → retorno a {HORIZONTE_D} días"),
             "rebote": deciles([{"f": e["f"], "t": e["t"]} for e in ev_reb], "f", "t",
                               f"REBOTE — caída ≥{DROP*100:.0f}% con volumen ≥{VOL_MULT}× → retorno a {HORIZONTE_H} horas")}
    json.dump(calib, open(os.path.join(HERE, "long_calib.json"), "w"), indent=1)
    print(f"\n# calibración -> long_calib.json")


if __name__ == "__main__":
    main()
