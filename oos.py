#!/usr/bin/env python3
"""
Validación FUERA DE MUESTRA: aplica la configuración exacta del bot a los 3 últimos años,
partidos en ventanas de 1 año, y compara la ventana más reciente (con la que se eligieron los
filtros) contra las anteriores (que nunca se miraron durante el estudio).

Si el resultado de la ventana reciente es mucho mejor que el de las anteriores, lo que tenemos
es sobreajuste, no una ventaja. Si se sostiene en las tres, hay algo real.

Configuración evaluada (idéntica a la del cron):
  short · volumen ≥1.2× · ADX≥25 (nivel premium en todas las TF) · nivel normal solo en 15m
  entrada al cierre de la vela que confirma el pivote · TP 2×ATR / SL 1×ATR · cooldown 4 h por par
"""
import argparse
import json
import os
import sys
import time

import backtest as B
import profit as P

HERE = os.path.dirname(os.path.abspath(__file__))
FEE = 0.001
TP, SL = 2.0, 1.0
DIA = 86_400_000
VOL_MULT = 1.2
MIN_ADX = 25.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=1095, help="3 años por defecto")
    ap.add_argument("--symbols", default="", help="por defecto, el universo del paso 1")
    ap.add_argument("--out", default=os.path.join(HERE, "oos_results.json"))
    args = ap.parse_args()

    prev = json.load(open(os.path.join(HERE, "backtest_results.json")))
    syms = [s.strip() for s in args.symbols.split(",") if s.strip()] or prev["meta"]["symbols"]
    now = int(time.time() * 1000)
    hi = (now // DIA) * DIA
    lo = hi - args.days * DIA
    warm = {iv: max(300, 60) * B.MS[iv] for iv in ("15m", "1h", "4h", "1d")}
    warm["1d"] = 400 * B.MS["1d"]
    print(f"# {len(syms)} pares · {args.days} días ({time.strftime('%Y-%m-%d', time.gmtime(lo/1000))} "
          f"→ {time.strftime('%Y-%m-%d', time.gmtime(hi/1000))})", file=sys.stderr)

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
                if s["dir"] != "short":
                    continue
                e = s["entry_idx"]
                if not (lo <= int(c[e][0]) < hi) or atr[e] is None or v_sma[e] is None:
                    continue
                a = adx[e] if e < len(adx) else None
                r = B.simulate(c, e, "short", s["atr"], B.MAX_BARS[iv], TP, SL)
                if r is None:
                    continue
                rows.append({"tf": iv, "sym": sym, "t": int(c[e][0]), "R0": r[0], "risk_pct": r[3],
                             "vol_ok": s["vol_ok"], "adx": (a[0] if a else None)})
        print(f"  {sym} listo ({len(rows)} señales acumuladas)", file=sys.stderr)

    n_win = max(1, args.days // 365)
    report = {"meta": {"generated": time.strftime("%Y-%m-%d %H:%M:%S %z"), "days": args.days,
                       "symbols": syms, "fees_pct_side": FEE * 100, "ventanas": n_win},
              "ventanas": {}, "por_tf": {}}

    def tier_premium(r):
        return r["vol_ok"] and r["adx"] is not None and r["adx"] >= MIN_ADX

    def tier_normal(r):
        return r["vol_ok"] and (r["adx"] is None or r["adx"] < MIN_ADX)

    def config_bot(r):
        # premium en todas las TF + normal solo en 15m (lo que corre el cron)
        return tier_premium(r) or (tier_normal(r) and r["tf"] == "15m")

    for w in range(n_win):
        w0 = lo + w * 365 * DIA
        w1 = lo + (w + 1) * 365 * DIA if w < n_win - 1 else hi
        etiqueta = f"{time.strftime('%Y-%m', time.gmtime(w0/1000))} → {time.strftime('%Y-%m', time.gmtime(w1/1000))}"
        sub = [r for r in rows if w0 <= r["t"] < w1]
        report["ventanas"][etiqueta] = {}
        for nombre, f in (("bot completo (premium + normal 15m)", config_bot),
                          ("solo premium (todas las TF)", tier_premium),
                          ("premium 4h", lambda r: tier_premium(r) and r["tf"] == "4h")):
            sel = sorted([r for r in sub if f(r)], key=lambda r: r["t"])
            lst, keep = {}, []
            for r in sel:
                if r["t"] - lst.get(r["sym"], 0) < B.COOLDOWN_H * 3600_000:
                    continue
                lst[r["sym"]] = r["t"]
                keep.append(r)
            m = P.stats(keep, FEE, 12.0)
            report["ventanas"][etiqueta][nombre] = m

    # detalle del bot completo por TF, en la ventana más reciente vs las anteriores
    print("\n=== ¿AGUANTA FUERA DE MUESTRA? (config exacta del bot, 1 año por ventana) ===")
    print("# la última ventana es la que se usó para elegir los filtros; las anteriores nunca se miraron")
    hdr = (f"{'ventana':<20} {'configuración':<36} {'alertas/mes':>11} {'n':>5} "
           f"{'al objetivo':>12} {'mueve/alert':>12}")
    print(hdr)
    print("-" * len(hdr))
    for etiqueta, cfg in report["ventanas"].items():
        for nombre, m in cfg.items():
            if not m or not m.get("n"):
                print(f"{etiqueta:<20} {nombre:<36} {'—':>11} {0:>5}   (sin señales)")
                continue
            print(f"{etiqueta:<20} {nombre:<36} {m['sigs_mes']:>11} {m['n']:>5} "
                  f"{m['winrate']:>11}% {m['mov_por_señal_pct']:>11}%")
        print()

    json.dump(report, open(args.out, "w"), indent=1)
    print(f"# detalle -> {args.out}")


if __name__ == "__main__":
    main()
