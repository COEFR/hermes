#!/usr/bin/env python3
"""
PASO 2 del estudio: traducir la esperanza en R a DINERO (¿es rentable mi cuenta?).

El paso 1 (backtest.py) respondio "¿con que frecuencia acierta?" y "¿que esperanza en R
tiene?". Eso no dice si la cuenta crece. Este script responde:

  1. EXIT SWEEP: la misma senal de divergencia de RSI con distintas salidas (TP x SL en
     multiplos de ATR). Sirve para separar "la senal no sirve" de "la salida elegida no sirve".
  2. CURVA DE CAPITAL: arriesgando un % fijo del capital por operacion (por defecto 1%),
     compuesto, en orden cronologico -> capital final, retorno total, drawdown maximo.
  3. ROBUSTEZ: parte la muestra en dos mitades temporales y desglosa por par, para ver si
     el resultado lo sostiene un solo par o un solo tramo del ano.

Reutiliza la cache de velas de backtest.py (mismo universo y misma ventana) para no
volver a descargar nada.
"""
import argparse
import json
import os
import sys
import time

import backtest as B

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_EXITS = "1.0/1.0,1.5/1.0,2.0/1.0,3.0/1.0,2.0/1.5,1.5/1.5"


def equity(trades, risk=0.01):
    """Capital final y drawdown maximo arriesgando `risk` del capital por operacion, compuesto."""
    eq, peak, dd = 1.0, 1.0, 0.0
    for R in trades:
        eq *= (1 + risk * R)
        peak = max(peak, eq)
        dd = min(dd, eq / peak - 1)
    return eq, dd


def wilson(k, n, z=1.96):
    if n == 0:
        return 0.0, 0.0, 0.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return p, max(0.0, c - h), min(1.0, c + h)


def stats(trades, fee, months):
    """trades: lista de dicts con R0, risk_pct, t, sym, dir, vol_ok, trend_ok, cooldown_ok."""
    n = len(trades)
    if n == 0:
        return None
    net = [t["R0"] - 2 * fee / t["risk_pct"] for t in trades]
    wins = [r for r in net if r > 0]
    gp = sum(wins)
    gl = -sum(r for r in net if r <= 0)
    p, lo, hi = wilson(len(wins), n)
    drag = 2 * fee / (sum(t["risk_pct"] for t in trades) / n)
    be = (1 + drag) / ((1.5 + drag + (1 + drag)))  # se recalcula abajo por config
    eq, dd = equity(net)
    moves = [t["R0"] * t["risk_pct"] * 100 - 2 * fee * 100 for t in trades]   # % de movimiento neto
    wm = [m for m, r in zip(moves, net) if r > 0]
    lm = [m for m, r in zip(moves, net) if r <= 0]
    return {
        "n": n, "winrate": round(100 * p, 1),
        "ic95": [round(100 * lo, 1), round(100 * hi, 1)],
        "exp_R": round(sum(net) / n, 3), "total_R": round(sum(net), 1),
        "PF": round(gp / gl, 2) if gl > 0 else None,
        "capital_final": round(eq, 3),
        "retorno_pct": round(100 * (eq - 1), 1),
        "dd_pct": round(100 * dd, 1),
        "retorno_mes_pct": round(100 * (eq ** (1 / max(months, 0.1)) - 1), 2),
        "sigs_mes": round(n / months, 1),
        "drag_R": round(drag, 3),
        "riesgo_medio_pct": round(100 * sum(t["risk_pct"] for t in trades) / n, 2),
        "mov_por_señal_pct": round(sum(moves) / n, 2),
        "acierto_mueve_pct": round(sum(wm) / len(wm), 2) if wm else 0,
        "fallo_mueve_pct": round(sum(lm) / len(lm), 2) if lm else 0,
        "ruina_50pct": eq < 0.5,
    }


def score_cells(report, min_n=30):
    """Mejor celda por configuración (se queda con la comisión más baja), ordenadas por esperanza."""
    best = {}
    for key, m in report["cells"].items():
        for fee in report["meta"]["fees_pct_side"]:
            d = m.get(f"fee{fee:.2f}%")
            if not d or d["n"] < min_n:
                continue
            if key not in best or d["exp_R"] > best[key][0]:
                best[key] = (d["exp_R"], key, fee, d)
    out = sorted(best.values(), key=lambda x: -x[0])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--risk", type=float, default=0.01, help="fraccion del capital por operacion")
    ap.add_argument("--exits", default=DEFAULT_EXITS, help="combinaciones TP/SL en multiplos de ATR")
    ap.add_argument("--fees", default="0.001,0.0005,0.0002")
    ap.add_argument("--out", default=os.path.join(HERE, "profit_results.json"))
    ap.add_argument("--top-robustez", type=int, default=3)
    args = ap.parse_args()

    exits = []
    for part in args.exits.split(","):
        tp, sl = part.strip().split("/")
        exits.append((float(tp), float(sl)))
    fees = [float(x) for x in args.fees.split(",")]
    months = args.days / 30.4

    try:
        prev = json.load(open(os.path.join(HERE, "backtest_results.json")))
        syms = prev["meta"]["symbols"]
        print(f"# universo (el mismo del paso 1, para reusar la cache): {len(syms)} pares", file=sys.stderr)
    except Exception:
        syms = B.top_symbols(15)

    now = int(time.time() * 1000)
    hi = (now // 86_400_000) * 86_400_000
    lo = hi - args.days * 86_400_000
    warm = {iv: max(300, 60) * B.MS[iv] for iv in ("15m", "1h", "4h", "1d")}
    warm["1d"] = 400 * B.MS["1d"]

    trades = {}          # (interval, tp, sl, variant) -> [trade, ...]
    for sym in syms:
        data = {}
        for iv in ("15m", "1h", "4h", "1d"):
            data[iv] = B.klines(sym, iv, lo - warm[iv], hi)
        for iv in ("15m", "1h", "4h", "1d"):
            c = data[iv]
            if len(c) < 400:
                continue
            closes = [float(x[4]) for x in c]
            rsi = B.rsi_wilder(closes)
            atr = B.atr_wilder(c)
            sigs = B.build_signals(c, rsi, atr)
            for s in sigs:
                s["sym"] = sym
            higher = data["4h"] if iv in ("15m", "1h") else (data["1d"] if iv == "4h" else None)
            if higher:
                B.apply_trend(sigs, higher)
            else:
                for s in sigs:
                    s["trend_ok"] = True
            for s in sigs:
                e = s["entry_idx"]
                if not (lo <= int(c[e][0]) < hi):
                    continue
                for (tp, sl) in exits:
                    r = B.simulate(c, e, s["dir"], s["atr"], B.MAX_BARS[iv], tp, sl)
                    if r is None:
                        continue
                    R0, bars, _pct, rp = r
                    row = {"R0": R0, "risk_pct": rp, "bars": bars, "t": int(c[e][0]),
                           "sym": sym, "dir": s["dir"], "vol_ok": s["vol_ok"],
                           "trend_ok": s["trend_ok"]}
                    for variant, ok in (("base", True),
                                        ("base+vol", s["vol_ok"]),
                                        ("base+vol+tend", s["vol_ok"] and s["trend_ok"])):
                        if ok:
                            trades.setdefault((iv, tp, sl, variant), []).append(dict(row))
        print(f"  {sym} listo", file=sys.stderr)

    # cooldown de 4h por par, por configuracion (usa la misma logica de dedupe que el bot real)
    for key, rows in trades.items():
        rows.sort(key=lambda r: r["t"])
        last = {}
        for r in rows:
            prev_t = last.get(r["sym"], 0)
            r["cooldown_ok"] = (r["t"] - prev_t) >= B.COOLDOWN_H * 3600_000
            last[r["sym"]] = r["t"]

    report = {"meta": {"generated": time.strftime("%Y-%m-%d %H:%M:%S %z"),
                       "days": args.days, "risk_per_trade_pct": args.risk * 100,
                       "fees_pct_side": [f * 100 for f in fees],
                       "exits_tp_sl": exits, "symbols": syms,
                       "celdas_totales_probadas": len(trades) * len(fees)},
              "cells": {}}

    for (iv, tp, sl, variant), rows in trades.items():
        cfg = f"{iv} TP{tp}/SL{sl} {variant}"
        report["cells"][cfg] = {"n_total": len(rows), "com_cooldown": sum(1 for r in rows if r["cooldown_ok"])}
        for fee in fees:
            m = stats(rows, fee, months)
            if m:
                report["cells"][cfg][f"fee{fee * 100:.2f}%"] = m

    ranked = score_cells(report)

    # ---- robustez de las mejores celdas: mitades temporales + por par ----
    robustness = {}
    for expR, cfg, fee, m in ranked[:args.top_robustez]:
        iv, tpsl, variant = cfg.split(" ")
        tp, sl = [float(x) for x in tpsl.replace("TP", "").split("/SL")]
        key = (iv, tp, sl, variant)
        rows = sorted(trades.get(key, []), key=lambda r: r["t"])
        if not rows:
            continue
        mid = rows[len(rows) // 2]["t"]
        h1 = [r for r in rows if r["t"] < mid]
        h2 = [r for r in rows if r["t"] >= mid]
        per_pair = {}
        for r in rows:
            per_pair.setdefault(r["sym"], []).append(r)
        robustness[cfg] = {
            "fee": fee,
            "todo": {k: m[k] for k in ("n", "winrate", "exp_R", "retorno_pct", "dd_pct")},
            "mitad_1": {k: v for k, v in (stats(h1, fee / 100, months / 2) or {}).items()
                        if k in ("n", "winrate", "exp_R", "retorno_pct")},
            "mitad_2": {k: v for k, v in (stats(h2, fee / 100, months / 2) or {}).items()
                        if k in ("n", "winrate", "exp_R", "retorno_pct")},
            "por_par": {sym: {"n": len(v), "exp_R": round(sum(x["R0"] - 2 * (fee / 100) / x["risk_pct"]
                                                             for x in v) / len(v), 3)}
                        for sym, v in sorted(per_pair.items(), key=lambda kv: -len(kv[1]))},
        }
    report["robustez"] = robustness

    # ---- CARTERAS: qué pasa si se opera todo junto vs solo 4h ----
    portfolios = {}
    for (tp, sl) in exits:
        rows_all, rows_4h, rows_4h1d = [], [], []
        for (iv, t_, s_, variant), rows in trades.items():
            if (t_, s_, variant) != (tp, sl, "base+vol"):
                continue
            rows_all += rows
            if iv == "4h":
                rows_4h += rows
            if iv in ("4h", "1d"):
                rows_4h1d += rows
        for name, rows in (("todas las TF (15m+1h+4h+1d)", rows_all),
                           ("solo 4h", rows_4h),
                           ("4h + 1d", rows_4h1d)):
            rows = sorted(rows, key=lambda r: r["t"])
            if not rows:
                continue
            for fee in fees:
                m = stats(rows, fee, months)
                if m:
                    portfolios.setdefault(f"TP{tp}/SL{sl} {name}", {})[f"fee{fee * 100:.2f}%"] = m
    report["carteras"] = portfolios
    json.dump(report, open(args.out, "w"), indent=1)

    # ---------------- salida ----------------
    print(f"\n=== ¿CRECE LA CUENTA? — arriesgando {args.risk*100:.0f}% del capital por operación, "
          f"compuesto, orden cronológico ({args.days} días) ===")
    print("# la divisa de la cuenta: capital final (1.00 = sin cambios), retorno del período y drawdown máximo")
    for fee in fees:
        fk = f"fee{fee * 100:.2f}%"
        print(f"\n--- comisión {fee*100:.2f}%/lado ---")
        hdr = (f"{'configuración':<34} {'n':>5} {'win%':>6} {'expR':>7} {'PF':>5} "
               f"{'capital':>8} {'retorno':>9} {'DD máx':>8} {'sig/mes':>8}")
        print(hdr)
        print("-" * len(hdr))
        for (iv, tp, sl, variant), rows in sorted(trades.items()):
            cfg = f"{iv} TP{tp}/SL{sl} {variant}"
            m = report["cells"][cfg].get(fk)
            if not m or not m["n"]:
                continue
            pf = m["PF"] if m["PF"] is not None else "inf"
            print(f"{cfg:<34} {m['n']:>5} {m['winrate']:>6} {m['exp_R']:>7} {str(pf):>5} "
                  f"{m['capital_final']:>8.3f} {m['retorno_pct']:>8}% {m['dd_pct']:>7}% {m['sigs_mes']:>8}")

    print(f"\n=== ROBUSTEZ de las {args.top_robustez} mejores configuraciones "
          f"(n>={30}; de {report['meta']['celdas_totales_probadas']} celdas probadas) ===")
    for cfg, r in robustness.items():
        t = r["todo"]
        print(f"\n▸ {cfg}  (comisión {r['fee']}/lado)")
        print(f"  completo : n={t['n']} win={t['winrate']}% expR={t['exp_R']} capital={1+t['retorno_pct']/100:.3f} DD={t['dd_pct']}%")
        print(f"  1ª mitad : {r['mitad_1']}")
        print(f"  2ª mitad : {r['mitad_2']}")
        pares = list(r["por_par"].items())
        print("  por par (5 con más señales): " + " · ".join(f"{s} n={d['n']} {d['exp_R']:+.2f}R" for s, d in pares[:5]))
    print("\n=== CARTERAS: ¿qué pasa si operás todo junto? (variante base+vol, riesgo 1%/trade) ===")
    hdr = (f"{'cartera':<40} {'comisión':>9} {'n':>5} {'win%':>6} {'expR':>7} "
           f"{'capital':>8} {'retorno':>9} {'DD máx':>8}")
    print(hdr)
    print("-" * len(hdr))
    for cfg, feesm in portfolios.items():
        for fk, m in feesm.items():
            print(f"{cfg:<40} {fk:>9} {m['n']:>5} {m['winrate']:>6} {m['exp_R']:>7} "
                  f"{m['capital_final']:>8.3f} {m['retorno_pct']:>8}% {m['dd_pct']:>7}%")

    # ---- lectura centrada en SEÑAL: sin cuenta, sin tamaño, solo % de movimiento ----
    print("\n=== EN TÉRMINOS DE SEÑAL: lo que se mueve el precio por alerta (sin cuenta ni tamaño) ===")
    print("# 'mueve' = % de movimiento del precio al resolver la señal, ya descontada la comisión")
    for fee in (fees[0], fees[1]):
        fk = f"fee{fee * 100:.2f}%"
        print(f"\n--- comisión {fee * 100:.2f}%/lado ---")
        hdr = (f"{'configuración':<30} {'alertas/mes':>11} {'aciertos':>9} {'si acierta':>11} "
               f"{'si falla':>10} {'mueve/alert':>12}")
        print(hdr)
        print("-" * len(hdr))
        for name, src in (("4h + volumen", report["cells"]),
                          ("cartera todas las TF", portfolios)):
            for cfg, m in src.items():
                if name == "4h + volumen":
                    if not (cfg.startswith("4h ") and cfg.endswith("base+vol")):
                        continue
                    d = m.get(fk)
                    label = cfg
                else:
                    if not cfg.endswith("todas las TF (15m+1h+4h+1d)"):
                        continue
                    d = m.get(fk)
                    label = cfg.replace(" todas las TF (15m+1h+4h+1d)", " (cartera)")
                if not d:
                    continue
                print(f"{label:<30} {d['sigs_mes']:>11} {d['winrate']:>8}% {d['acierto_mueve_pct']:>10}% "
                      f"{d['fallo_mueve_pct']:>9}% {d['mov_por_señal_pct']:>11}%")

    print(f"\n# resultados completos -> {args.out}")


if __name__ == "__main__":
    main()
