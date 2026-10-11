#!/usr/bin/env python3
"""
Calibra el PUNTAJE 1-10 de las señales midiendo, con datos reales, qué características separan
los buenos resultados de los malos. No hay pesos inventados: cada característica entra por el
tramo que le corresponde, con el valor promedio de % de movimiento que ese tramo rindió en el
histórico, y pesa lo que pesa su dispersión medida.

Salida: score_calib.json (lo lee `signals.py` para puntuar cada alerta en vivo).
Reporte: tabla por característica, deciles del puntaje compuesto y control en dos mitades.
"""
import argparse
import json
import os
import sys
import time

import backtest as B
import profit as P
import scoring as S

HERE = os.path.dirname(os.path.abspath(__file__))
FEE = 0.001
TP, SL = 2.0, 1.0
VOL_MULT = 1.2
MIN_SPREAD = 0.08          # dispersión mínima (en puntos %) para que una característica sume


def percentiles(vals, qs):
    xs = sorted(vals)
    out = []
    for q in qs:
        i = min(len(xs) - 1, max(0, int(round(q * (len(xs) - 1)))))
        out.append(xs[i])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--symbols", default="")
    ap.add_argument("--out", default=S.CALIB_PATH)
    args = ap.parse_args()

    prev = json.load(open(os.path.join(HERE, "backtest_results.json")))
    syms = [s.strip() for s in args.symbols.split(",") if s.strip()] or prev["meta"]["symbols"]
    now = int(time.time() * 1000)
    hi = (now // 86_400_000) * 86_400_000
    lo = hi - args.days * 86_400_000
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
            obv = B.obv_slope(c)
            v_sma = B.sma(vols, 20)
            lo_p, hi_p = B.pivots(c)
            for i in range(1, len(hi_p)):
                p1, p2 = hi_p[i - 1], hi_p[i]
                if p2 - p1 > B.PIVOT_LOOKBACK or p2 - p1 < B.PIVOT_K * 2:
                    continue
                e = p2 + B.PIVOT_K
                if not (lo <= int(c[e][0]) < hi) or rsi[p1] is None or rsi[p2] is None:
                    continue
                if atr[e] is None or v_sma[e] is None:
                    continue
                v1, v2 = float(c[p1][2]), float(c[p2][2])
                if not (v2 > v1 and (v2 - v1) / v1 >= B.MIN_PRICE_DIFF):
                    continue
                if not (rsi[p2] < rsi[p1] - B.MIN_RSI_DIFF):
                    continue
                vol_ratio = vols[e] / v_sma[e] if v_sma[e] else 0
                if vol_ratio < VOL_MULT:            # puerta base: la misma del bot
                    continue
                a = adx[e] if e < len(adx) else None
                r = B.simulate(c, e, "short", atr[e], B.MAX_BARS[iv], TP, SL)
                if r is None:
                    continue
                R0, _bars, _g, rp = r
                R_net = R0 - 2 * FEE / rp
                sig = {"adx": a[0] if a else None, "plus_di": a[1] if a else None,
                       "minus_di": a[2] if a else None, "vol_ratio": vol_ratio,
                       "obv": obv[e] if e < len(obv) else None, "atr_pct": 100 * rp,
                       "rsi_now": rsi[p2], "rsi_prev": rsi[p1], "px_prev": v1, "px_now": v2,
                       "pivot_gap": p2 - p1}
                f = S.features(sig)
                rows.append({"tf": iv, "t": int(c[e][0]), "mov_pct": R_net * rp * 100,
                             "win": 1 if R_net > 0 else 0, "f": f})
        print(f"  {sym} listo ({len(rows)} señales)", file=sys.stderr)

    n = len(rows)
    print(f"\n=== CALIBRACIÓN DEL PUNTAJE — {n} señales de short con volumen confirmado "
          f"({args.days} días, {len(syms)} pares) ===")
    if n < 40:
        print("!! muestra insuficiente para calibrar (se necesitan 40+ señales)")
        return

    # ---- 1) por característica: tertiles y su % de movimiento medio ----
    print("\n--- qué separa los buenos resultados (tertiles) ---")
    hdr = f"{'característica':<12} {'tramo bajo':>11} {'medio':>11} {'alto':>11} {'dispersión':>11}"
    print(hdr)
    print("-" * len(hdr))
    calib_feats, spreads = {}, {}
    for feat in S.FEATURES:
        vals = [r["f"][feat] for r in rows]
        b = [round(x, 4) for x in percentiles(vals, [1 / 3, 2 / 3])]
        k1, k2, k3 = f"<{b[0]}", f"<{b[1]}", f">={b[1]}"
        grupos = {k1: [], k2: [], k3: []}
        for r in rows:
            grupos[S._step_key({"boundaries": b}, r["f"][feat])].append(r["mov_pct"])
        lookup = {k: (sum(v) / len(v) if v else 0.0) for k, v in grupos.items()}
        lookup = {k: round(v, 4) for k, v in lookup.items()}
        means = [lookup[k1], lookup[k2], lookup[k3]]
        spread = max(means) - min(means)
        rangos = {k1: f"<{b[0]:.4g}", k2: f"{b[0]:.4g}–{b[1]:.4g}", k3: f"≥{b[1]:.4g}"}
        calib_feats[feat] = {"fn": "step", "boundaries": b, "lookup": lookup,
                             "label": feat, "spread": round(spread, 3),
                             "n_por_tramo": {k: len(v) for k, v in grupos.items()},
                             "rangos": rangos}
        spreads[feat] = spread
        print(f"{feat:<12} {means[0]:>10.2f}% {means[1]:>10.2f}% {means[2]:>10.2f}% {spread:>10.2f}%")

    # ---- 2) pesos: proporcional a la dispersión medida, descartando las que no separan ----
    utiles = {k: v for k, v in spreads.items() if v >= MIN_SPREAD}
    total = sum(utiles.values()) or 1.0
    weights = {k: round(v / total, 4) for k, v in utiles.items()}
    for k in spreads:
        weights.setdefault(k, 0.0)
    print(f"\ncaracterísticas que entran al puntaje (dispersión ≥ {MIN_SPREAD}%): "
          + ", ".join(f"{k} {v*100:.0f}%" for k, v in sorted(weights.items(), key=lambda kv: -kv[1])
                      if v > 0))

    # ---- 3) compuesto y deciles ----
    calib = {"features": calib_feats, "weights": weights, "decile_cuts": [], "deciles": [],
             "meta": {"generated": time.strftime("%Y-%m-%d %H:%M:%S %z"), "days": args.days,
                      "symbols": syms, "n": n, "fee_pct_side": FEE * 100, "tp": TP, "sl": SL,
                      "filtros": f"short + volumen ≥{VOL_MULT}× (puerta base)"}}
    for r in rows:
        r["comp"] = S.composite(r["f"], calib)
    comps = sorted(r["comp"] for r in rows)
    cuts = [comps[min(n - 1, int(round(q * n)))] for q in [i / 10 for i in range(1, 10)]]
    calib["decile_cuts"] = [round(c, 4) for c in cuts]
    for r in rows:
        r["score"] = S.score_1_10(r["f"], calib)["score"]

    months = args.days / 30.4
    print("\n--- puntaje vs resultado real (deciles) ---")
    hdr = (f"{'puntaje':<9} {'etiqueta':<12} {'n':>5} {'señales/mes':>12} {'al objetivo':>12} "
           f"{'mueve/alert':>12}")
    print(hdr)
    print("-" * len(hdr))
    for s in range(1, 11):
        grp = [r for r in rows if r["score"] == s]
        if not grp:
            continue
        win = 100 * sum(r["win"] for r in grp) / len(grp)
        mov = sum(r["mov_pct"] for r in grp) / len(grp)
        d = {"score": s, "etiqueta": dict(S.ETIQUETAS)[s], "n": len(grp),
             "sigs_mes": round(len(grp) / months, 1), "winrate": round(win, 1),
             "mov_por_señal_pct": round(mov, 2)}
        calib["deciles"].append(d)
        print(f"{s:<9} {d['etiqueta']:<12} {len(grp):>5} {d['sigs_mes']:>12} {win:>11.1f}% "
              f"{mov:>11.2f}%")

    # ---- 4) control en dos mitades: el puntaje debe ordenar en AMBAS ----
    mid = sorted(r["t"] for r in rows)[n // 2]
    print("\n--- control: los puntajes altos separan también en cada mitad del período? ---")
    for nombre, sub in (("1ª mitad", [r for r in rows if r["t"] < mid]),
                        ("2ª mitad", [r for r in rows if r["t"] >= mid])):
        altos = [r["mov_pct"] for r in sub if r["score"] >= 7]
        bajos = [r["mov_pct"] for r in sub if r["score"] <= 4]
        f_altos = sum(altos) / len(altos) if altos else 0
        f_bajos = sum(bajos) / len(bajos) if bajos else 0
        calib.setdefault("control_mitades", {})[nombre] = {
            "altos_n": len(altos), "altos_mov": round(f_altos, 2),
            "bajos_n": len(bajos), "bajos_mov": round(f_bajos, 2)}
        print(f"  {nombre}: puntaje ≥7 → {f_altos:+.2f}% (n={len(altos)})  ·  "
              f"puntaje ≤4 → {f_bajos:+.2f}% (n={len(bajos)})")

    json.dump(calib, open(args.out, "w"), indent=1)
    print(f"\n# calibración -> {args.out}")


if __name__ == "__main__":
    main()
