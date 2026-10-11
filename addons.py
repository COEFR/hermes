#!/usr/bin/env python3
"""
PASO 3a: ¿qué se le puede AGREGAR a la estrategia de RSI + divergencias para que sea rentable?

Cada candidato se mide contra los mismos datos (15 pares, 1 año, cache de backtest.py) y se
compara contra la referencia = la señal + filtro de volumen + salida TP2.0/SL1.0 (lo mejor del
paso 2). Se reporta en idioma de señal: alertas/mes, % de aciertos y % de movimiento por alerta.

Candidatos (filtros que se añaden a la señal):
  volatilidad mínima (ATR >= 1% / 1.5% / 2% del precio)  -> la comisión pesa menos frente al stop
  RSI en extremo (<35 compra / >65 venta)
  divergencia más ancha (>= 20 velas entre pivotes)
  régimen: BTC sobre/bajo su EMA200 de 4h a favor de la señal
  vela de confirmación (el cierre acompaña la dirección de la señal)
Candidatos (cambios en la SALIDA):
  trailing 2xATR desde el extremo (sin objetivo fijo)
  stop a break-even tras +1R, con objetivo 2xATR
"""
import json
import os
import sys
import time

import backtest as B
import profit as P

HERE = os.path.dirname(os.path.abspath(__file__))
FEE = 0.001
FEE2 = 0.0005
TP_MULT, SL_MULT = 2.0, 1.0


def sim_trail(c, e, direction, a, max_bars, mult=2.0):
    """Trailing stop a `mult` ATR del extremo alcanzado, sin objetivo fijo."""
    entry = float(c[e][4])
    if a <= 0 or entry <= 0:
        return None
    ext = entry
    exit_px = None
    end = min(len(c), e + 1 + max_bars)
    for j in range(e + 1, end):
        h, l = float(c[j][2]), float(c[j][3])
        if direction == "long":
            ext = max(ext, h)
            stop = ext - mult * a
            if l <= stop:
                exit_px = stop
                break
        else:
            ext = min(ext, l)
            stop = ext + mult * a
            if h >= stop:
                exit_px = stop
                break
    if exit_px is None:
        exit_px = float(c[end - 1][4])
    gross = (exit_px / entry - 1) if direction == "long" else (entry / exit_px - 1)
    rp = a / entry
    return gross / rp, end - 1 - e, 100 * gross, rp


def sim_be(c, e, direction, a, max_bars, tp_mult=2.0):
    """Stop a break-even despues de alcanzar +1R; objetivo tp_mult ATR."""
    entry = float(c[e][4])
    if a <= 0 or entry <= 0:
        return None
    sl = entry - a if direction == "long" else entry + a
    tp = entry + tp_mult * a if direction == "long" else entry - tp_mult * a
    armed = False
    exit_px = None
    end = min(len(c), e + 1 + max_bars)
    for j in range(e + 1, end):
        h, l = float(c[j][2]), float(c[j][3])
        if not armed:
            reached = (h >= entry + a) if direction == "long" else (l <= entry - a)
            if reached:
                sl = entry
                armed = True
        hit_sl = (l <= sl) if direction == "long" else (h >= sl)
        hit_tp = (h >= tp) if direction == "long" else (l <= tp)
        if hit_sl:
            exit_px = sl
            break
        if hit_tp:
            exit_px = tp
            break
    if exit_px is None:
        exit_px = float(c[end - 1][4])
    gross = (exit_px / entry - 1) if direction == "long" else (entry / exit_px - 1)
    rp = a / entry
    return gross / rp, end - 1 - e, 100 * gross, rp


def main():
    days = 365
    prev = json.load(open(os.path.join(HERE, "backtest_results.json")))
    syms = prev["meta"]["symbols"]
    months = days / 30.4

    now = int(time.time() * 1000)
    hi = (now // 86_400_000) * 86_400_000
    lo = hi - days * 86_400_000
    warm = {iv: max(300, 60) * B.MS[iv] for iv in ("15m", "1h", "4h", "1d")}
    warm["1d"] = 400 * B.MS["1d"]

    btc4h = B.klines("BTCUSDT", "4h", lo - warm["4h"], hi)

    rows = []          # senales base+vol con todos los atributos extra
    for sym in syms:
        data = {iv: B.klines(sym, iv, lo - warm[iv], hi) for iv in ("15m", "1h", "4h", "1d")}
        for iv in ("15m", "1h", "4h", "1d"):
            c = data[iv]
            if len(c) < 400:
                continue
            closes = [float(x[4]) for x in c]
            rsi = B.rsi_wilder(closes)
            atr = B.atr_wilder(c)
            sigs = B.build_signals(c, rsi, atr)
            if not sigs:
                continue
            for s in sigs:
                s["sym"] = sym
            B.apply_trend(sigs, btc4h)          # régimen del mercado (BTC vs su EMA200 de 4h)
            for s in sigs:
                e = s["entry_idx"]
                if not (lo <= int(c[e][0]) < hi) or not s["vol_ok"]:
                    continue
                entry = float(c[e][4])
                o, h, l, cl = (float(c[e][1]), float(c[e][2]), float(c[e][3]), float(c[e][4]))
                for name, fn in (("tp2", lambda: B.simulate(c, e, s["dir"], s["atr"], B.MAX_BARS[iv], TP_MULT, SL_MULT)),
                                 ("trail2", lambda: sim_trail(c, e, s["dir"], s["atr"], B.MAX_BARS[iv], 2.0)),
                                 ("be_tp2", lambda: sim_be(c, e, s["dir"], s["atr"], B.MAX_BARS[iv], 2.0))):
                    r = fn()
                    if r is None:
                        continue
                    R0, bars, _g, rp = r
                    rows.append({
                        "exit": name, "R0": R0, "risk_pct": rp, "bars": bars,
                        "t": int(c[e][0]), "sym": sym, "tf": iv, "dir": s["dir"],
                        "vol_ok": True, "btc_ok": bool(s["trend_ok"]),
                        "rsi_extremo": (s["rsi_p"] < 35) if s["dir"] == "long" else (s["rsi_p"] > 65),
                        "ancha": (s["idx"] - s["p1_idx"]) >= 20,
                        "confirm": (cl > o) if s["dir"] == "long" else (cl < o),
                        "atr_pct": 100 * rp, "riesgo_pct": 100 * rp,
                    })
        print(f"  {sym} listo", file=sys.stderr)

    filtros = {
        "referencia (señal + volumen + salida TP2)": lambda r: r["exit"] == "tp2",
        "+ volatilidad ATR ≥ 1%": lambda r: r["exit"] == "tp2" and r["atr_pct"] >= 1.0,
        "+ volatilidad ATR ≥ 1.5%": lambda r: r["exit"] == "tp2" and r["atr_pct"] >= 1.5,
        "+ volatilidad ATR ≥ 2%": lambda r: r["exit"] == "tp2" and r["atr_pct"] >= 2.0,
        "+ RSI en extremo (<35 / >65)": lambda r: r["exit"] == "tp2" and r["rsi_extremo"],
        "+ divergencia ancha (≥20 velas)": lambda r: r["exit"] == "tp2" and r["ancha"],
        "+ BTC a favor (EMA200 4h)": lambda r: r["exit"] == "tp2" and r["btc_ok"],
        "+ vela de confirmación": lambda r: r["exit"] == "tp2" and r["confirm"],
        "salida: trailing 2×ATR (sin objetivo)": lambda r: r["exit"] == "trail2",
        "salida: break-even tras +1R, TP 2×ATR": lambda r: r["exit"] == "be_tp2",
    }
    combinados = [
        ("vol + ATR≥1.5% + RSI extremo", lambda r: r["exit"] == "tp2" and r["atr_pct"] >= 1.5 and r["rsi_extremo"]),
        ("vol + ATR≥1.5% + BTC a favor", lambda r: r["exit"] == "tp2" and r["atr_pct"] >= 1.5 and r["btc_ok"]),
        ("vol + ATR≥1.5% + RSI extremo + BTC", lambda r: r["exit"] == "tp2" and r["atr_pct"] >= 1.5
         and r["rsi_extremo"] and r["btc_ok"]),
    ]

    out = {}
    print("\n=== ¿QUÉ AGREGAR? Comparado con la referencia (señal + volumen, salida TP2/SL1) ===")
    print("# 'mueve/alert' = % de movimiento esperado del precio por alerta, comisión 0.10%/lado ya descontada")
    for tf in ("15m", "1h", "4h", "1d"):
        base = [r for r in rows if r["tf"] == tf]
        if not base:
            continue
        print(f"\n--- {tf} ---")
        hdr = (f"{'qué se agrega':<42} {'alertas/mes':>11} {'n':>5} {'aciertos':>9} "
               f"{'si acierta':>10} {'si falla':>9} {'mueve/alert':>11}")
        print(hdr)
        print("-" * len(hdr))
        for name, f in list(filtros.items()) + combinados:
            sub = [r for r in base if f(r)]
            m = P.stats(sub, FEE, months)
            if not m or m["n"] < 8:
                out[f"{tf}|{name}"] = {"n": m["n"] if m else 0}
                print(f"{name:<42} {'—':>11} {m['n'] if m else 0:>5}   (muestra insuficiente)")
                continue
            out[f"{tf}|{name}"] = {k: m[k] for k in ("n", "sigs_mes", "winrate", "exp_R",
                                                     "acierto_mueve_pct", "fallo_mueve_pct",
                                                     "mov_por_señal_pct")}
            print(f"{name:<42} {m['sigs_mes']:>11} {m['n']:>5} {m['winrate']:>8}% "
                  f"{m['acierto_mueve_pct']:>9}% {m['fallo_mueve_pct']:>8}% {m['mov_por_señal_pct']:>10}%")

    print("\n=== RECETAS listas para el bot (filtros combinados) ===")
    print("# 'mitades' = mismo cálculo partiendo el año en dos, para ver si aguanta o es un tramo afortunado")
    recetas = {
        "4h + vol + ATR≥1.5%": lambda r: r["exit"] == "tp2" and r["tf"] == "4h" and r["atr_pct"] >= 1.5,
        "15m + vol + ATR≥1.5%": lambda r: r["exit"] == "tp2" and r["tf"] == "15m" and r["atr_pct"] >= 1.5,
        "1h + vol + ATR≥1.5%": lambda r: r["exit"] == "tp2" and r["tf"] == "1h" and r["atr_pct"] >= 1.5,
        "15m+4h + vol + ATR≥1.5%": lambda r: r["exit"] == "tp2" and r["tf"] in ("15m", "4h") and r["atr_pct"] >= 1.5,
        "15m+4h + vol + ATR≥1.5% + RSI extremo": lambda r: r["exit"] == "tp2" and r["tf"] in ("15m", "4h")
        and r["atr_pct"] >= 1.5 and r["rsi_extremo"],
        "solo 4h + vol + ATR≥2%": lambda r: r["exit"] == "tp2" and r["tf"] == "4h" and r["atr_pct"] >= 2.0,
    }
    hdr = (f"{'receta':<44} {'alertas/mes':>11} {'n':>5} {'aciertos':>9} {'mueve/alert':>12} "
           f"{'1ª mitad':>10} {'2ª mitad':>10}")
    print(hdr)
    print("-" * len(hdr))
    for name, f in recetas.items():
        sub = sorted([r for r in rows if f(r)], key=lambda r: r["t"])
        m = P.stats(sub, FEE, months)
        if not m or m["n"] < 8:
            print(f"{name:<44} {'—':>11} {m['n'] if m else 0:>5}   (muestra insuficiente)")
            continue
        mid = sub[len(sub) // 2]["t"]
        h1 = P.stats([r for r in sub if r["t"] < mid], FEE, months / 2)
        h2 = P.stats([r for r in sub if r["t"] >= mid], FEE, months / 2)
        out[f"RECETA|{name}"] = {k: m[k] for k in ("n", "sigs_mes", "winrate", "exp_R",
                                                   "acierto_mueve_pct", "fallo_mueve_pct",
                                                   "mov_por_señal_pct")}
        if h1 and h2:
            out[f"RECETA|{name}"]["mitad_1_mueve"] = h1["mov_por_señal_pct"]
            out[f"RECETA|{name}"]["mitad_2_mueve"] = h2["mov_por_señal_pct"]
            print(f"{name:<44} {m['sigs_mes']:>11} {m['n']:>5} {m['winrate']:>8}% "
                  f"{m['mov_por_señal_pct']:>11}% {h1['mov_por_señal_pct']:>9}% {h2['mov_por_señal_pct']:>9}%")
        else:
            print(f"{name:<44} {m['sigs_mes']:>11} {m['n']:>5} {m['winrate']:>8}% "
                  f"{m['mov_por_señal_pct']:>11}% {'—':>10} {'—':>10}")

    json.dump(out, open(os.path.join(HERE, "addons_results.json"), "w"), indent=1)
    print(f"\n# detalle -> {os.path.join(HERE, 'addons_results.json')}")


if __name__ == "__main__":
    main()
