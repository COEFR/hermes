#!/usr/bin/env python3
"""
Estudio de las señales de SHORT (divergencia bajista de RSI) con ADX y volumen.

Responde: ¿qué umbral de ADX y qué confirmación de volumen conviene para las alertas de
short? Se mide sobre los mismos datos (15 pares, 1 año) y en idioma de señal: alertas/mes,
% de aciertos y % de movimiento por alerta (comisión 0.10%/lado descontada).

Salida: shorts_results.json — lo consume signals.py para poner el histórico en cada alerta.
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
DAYS = 365
LADO = "short"


def main():
    global DAYS
    ap = argparse.ArgumentParser()
    ap.add_argument("--side", default="short", choices=["short", "long"],
                    help="lado a medir")
    ap.add_argument("--relax", action="store_true",
                    help="umbrales de divergencia más laxos (0.2%% de precio y 1 punto de RSI)")
    ap.add_argument("--days", type=int, default=DAYS, help="ventana en días (365 por defecto)")
    args = ap.parse_args()
    DAYS = args.days
    if args.relax:
        B.MIN_PRICE_DIFF = 0.002
        B.MIN_RSI_DIFF = 1.0
        print("# UMBRALES RELAJADOS: precio 0.2%, RSI 1 punto")
    global LADO
    LADO = args.side
    print(f"# lado medido: {LADO.upper()}")
    prev = json.load(open(os.path.join(HERE, "backtest_results.json")))
    syms = prev["meta"]["symbols"]
    months = DAYS / 30.4
    now = int(time.time() * 1000)
    hi = (now // 86_400_000) * 86_400_000
    lo = hi - DAYS * 86_400_000
    warm = {iv: max(300, 60) * B.MS[iv] for iv in ("15m", "1h", "4h", "1d")}
    warm["1d"] = 400 * B.MS["1d"]

    rows = []
    total_shorts = 0
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
            sigs = [s for s in B.build_signals(c, rsi, atr) if s["dir"] == LADO]
            v_sma = B.sma(vols, 20)
            for s in sigs:
                e = s["entry_idx"]
                if not (lo <= int(c[e][0]) < hi):
                    continue
                total_shorts += 1
                a = adx[e] if e < len(adx) else None
                r = B.simulate(c, e, LADO, s["atr"], B.MAX_BARS[iv], TP, SL)
                if r is None:
                    continue
                R0, bars, _g, rp = r
                rows.append({
                    "tf": iv, "sym": sym, "t": int(c[e][0]), "R0": R0, "risk_pct": rp,
                    "vol_ok": s["vol_ok"],
                    "vol_ratio": (vols[e] / v_sma[e]) if v_sma[e] else 0,
                    "adx": a[0] if a else None,
                    "mdi_gt_pdi": (a[2] > a[1]) if a else None,
                    "obv_neg": (obv[e] < 0) if (e < len(obv) and obv[e] is not None) else None,
                    "atr_pct": 100 * rp,
                })
        print(f"  {sym} listo", file=sys.stderr)

    combos = {
        "sin filtros (solo divergencia bajista)": lambda r: True,
        "+ volumen ≥1.2×": lambda r: r["vol_ok"],
        "+ ADX ≥ 20": lambda r: r["adx"] is not None and r["adx"] >= 20,
        "+ ADX ≥ 25": lambda r: r["adx"] is not None and r["adx"] >= 25,
        "+ ADX ≥ 30": lambda r: r["adx"] is not None and r["adx"] >= 30,
        "+ volumen ≥1.2× y ADX ≥ 20": lambda r: r["vol_ok"] and r["adx"] is not None and r["adx"] >= 20,
        "+ volumen ≥1.2× y ADX ≥ 25": lambda r: r["vol_ok"] and r["adx"] is not None and r["adx"] >= 25,
        "+ vol ≥1.2× + ADX≥25 + −DI>+DI": lambda r: r["vol_ok"] and r["adx"] is not None
        and r["adx"] >= 25 and r["mdi_gt_pdi"],
        "+ vol ≥1.2× + ADX≥25 + −DI>+DI + OBV<0": lambda r: r["vol_ok"] and r["adx"] is not None
        and r["adx"] >= 25 and r["mdi_gt_pdi"] and r["obv_neg"],
        "+ vol ≥1.2× + ADX≥25 + ATR ≥1.5%": lambda r: r["vol_ok"] and r["adx"] is not None
        and r["adx"] >= 25 and r["atr_pct"] >= 1.5,
        "+ vol ≥1.2× + ADX≥25 + −DI>+DI + ATR ≥1.5%": lambda r: r["vol_ok"] and r["adx"] is not None
        and r["adx"] >= 25 and r["mdi_gt_pdi"] and r["atr_pct"] >= 1.5,
    }

    out = {"meta": {"generated": time.strftime("%Y-%m-%d %H:%M:%S %z"), "days": DAYS,
                    "shorts_detectados": total_shorts, "tp": TP, "sl": SL,
                    "fee_pct_side": FEE * 100}, "por_tf": {}, "todo_junto": {}}

    print(f"\n=== SHORTS (divergencia bajista de RSI) — {total_shorts} señales detectadas en el año ===")
    print("# 'mueve/alert' = % de movimiento del precio por alerta, comisión 0.10%/lado ya descontada")
    for tf in ("15m", "1h", "4h", "1d"):
        base = [r for r in rows if r["tf"] == tf]
        if len(base) < 20:
            continue
        print(f"\n--- {tf} ---")
        hdr = f"{'filtros':<46} {'alertas/mes':>11} {'n':>5} {'aciertos':>9} {'mueve/alert':>12}"
        print(hdr)
        print("-" * len(hdr))
        out["por_tf"][tf] = {}
        for name, f in combos.items():
            sub = sorted([r for r in base if f(r)], key=lambda r: r["t"])
            m = P.stats(sub, FEE, months)
            if not m or m["n"] < 10:
                out["por_tf"][tf][name] = {"n": m["n"] if m else 0}
                print(f"{name:<46} {'—':>11} {m['n'] if m else 0:>5}   (muestra insuficiente)")
                continue
            mid = sub[len(sub) // 2]["t"]
            h1 = P.stats([r for r in sub if r["t"] < mid], FEE, months / 2)
            h2 = P.stats([r for r in sub if r["t"] >= mid], FEE, months / 2)
            out["por_tf"][tf][name] = {
                "n": m["n"], "sigs_mes": m["sigs_mes"], "winrate": m["winrate"],
                "acierto_mueve_pct": m["acierto_mueve_pct"], "fallo_mueve_pct": m["fallo_mueve_pct"],
                "mov_por_señal_pct": m["mov_por_señal_pct"], "exp_R": m["exp_R"],
                "mitad_1_mueve": h1["mov_por_señal_pct"] if h1 else None,
                "mitad_2_mueve": h2["mov_por_señal_pct"] if h2 else None}
            print(f"{name:<46} {m['sigs_mes']:>11} {m['n']:>5} {m['winrate']:>8}% "
                  f"{m['mov_por_señal_pct']:>11}%")

    # resumen de la configuracion elegida, para el mensaje de alerta
    elegido = "+ volumen ≥1.2× y ADX ≥ 25"
    print(f"\n=== CONFIGURACIÓN ELEGIDA: {elegido} (salida 2×ATR / 1×ATR) ===")
    for tf in ("15m", "1h", "4h", "1d"):
        d = (out["por_tf"].get(tf) or {}).get(elegido)
        if d and d.get("n", 0) >= 10:
            out["todo_junto"][tf] = d
            print(f"  {tf}: {d['sigs_mes']} alertas/mes · {d['winrate']}% aciertos · "
                  f"{d['mov_por_señal_pct']}% por alerta · mitades {d['mitad_1_mueve']}% / {d['mitad_2_mueve']}%")

    # ---- la configuración EXACTA del bot: short + vol + ADX≥25, con cooldown de 4 h por par ----
    filtro_bot = combos["+ volumen ≥1.2× y ADX ≥ 25"]
    print("\n=== LA ESTRATEGIA DEL BOT, medida tal cual quedó (con cooldown de 4 h por par) ===")
    hdr = (f"{'temporalidades':<18} {'alertas/mes':>11} {'n':>5} {'al objetivo':>12} "
           f"{'si acierta':>11} {'si falla':>9} {'mueve/alert':>12} {'1ª mitad':>9} {'2ª mitad':>9}")
    print(hdr)
    print("-" * len(hdr))
    combos_bot = [(("15m", "1h", "4h", "1d"), 0.0), (("15m", "1h", "4h", "1d"), 1.5),
                  (("15m",), 0.0), (("15m",), 1.5), (("1h", "4h", "1d"), 0.0),
                  (("4h",), 0.0), (("1h",), 0.0)]
    for tfs, atr_min in combos_bot:
        label = "+".join(tfs) + (f" +ATR≥{atr_min:g}%" if atr_min else "")
        sub = sorted([r for r in rows if r["tf"] in tfs and filtro_bot(r)
                      and r["atr_pct"] >= atr_min], key=lambda r: r["t"])
        last = {}
        keep = []
        for r in sub:                                  # cooldown 4 h por par, igual que el bot
            if r["t"] - last.get(r["sym"], 0) < B.COOLDOWN_H * 3600_000:
                continue
            last[r["sym"]] = r["t"]
            keep.append(r)
        m = P.stats(keep, FEE, months)
        if not m or m["n"] < 5:
            print(f"{label:<18} {'—':>11} {m['n'] if m else 0:>5}   (muestra insuficiente)")
            continue
        mid = keep[len(keep) // 2]["t"]
        h1 = P.stats([r for r in keep if r["t"] < mid], FEE, months / 2)
        h2 = P.stats([r for r in keep if r["t"] >= mid], FEE, months / 2)
        out["bot_config"] = out.get("bot_config", {})
        out["bot_config"][label] = {
            "n": m["n"], "sigs_mes": m["sigs_mes"], "winrate": m["winrate"],
            "acierto_mueve_pct": m["acierto_mueve_pct"], "fallo_mueve_pct": m["fallo_mueve_pct"],
            "mov_por_señal_pct": m["mov_por_señal_pct"],
            "mitad_1_mueve": h1["mov_por_señal_pct"] if h1 else None,
            "mitad_2_mueve": h2["mov_por_señal_pct"] if h2 else None}
        print(f"{label:<18} {m['sigs_mes']:>11} {m['n']:>5} {m['winrate']:>11}% "
              f"{m['acierto_mueve_pct']:>10}% {m['fallo_mueve_pct']:>8}% {m['mov_por_señal_pct']:>11}% "
              f"{(h1['mov_por_señal_pct'] if h1 else 0):>8}% {(h2['mov_por_señal_pct'] if h2 else 0):>8}%")

    # ---- embudo: dónde se pierden las señales (solo las temporalidades del bot) ----
    tfs_bot = ("1h", "4h", "1d")
    base = [r for r in rows if r["tf"] in tfs_bot]
    print("\n=== EMBUDO: por qué el bot da pocas alertas (1h+4h+1d, 1 año, 15 pares) ===")
    pasos = [
        ("divergencias bajistas detectadas", lambda r: True),
        ("+ volumen ≥1.2× la media", lambda r: r["vol_ok"]),
        ("+ ADX ≥ 25", lambda r: r["vol_ok"] and r["adx"] is not None and r["adx"] >= 25),
    ]
    for name, f in pasos:
        n = sum(1 for r in base if f(r))
        pct = f"{100 * n / len(base):.0f}%" if base else "-"
        print(f"  {name:<36} {n:>5} señales  ({pct:>4} del total)   ≈ {n / months:.1f}/mes")
    kept = 0
    last = {}
    for r in sorted([r for r in base if pasos[-1][1](r)], key=lambda r: r["t"]):
        if r["t"] - last.get(r["sym"], 0) < B.COOLDOWN_H * 3600_000:
            continue
        last[r["sym"]] = r["t"]
        kept += 1
    print(f"  {'+ cooldown 4 h por par':<36} {kept:>5} señales  ({100 * kept / len(base):>4.0f}% del total)   "
          f"≈ {kept / months:.1f}/mes  ← lo que manda el bot")
    for tf in tfs_bot:
        sub = [r for r in base if r["tf"] == tf]
        n1 = sum(1 for r in sub if r["vol_ok"])
        n2 = sum(1 for r in sub if r["vol_ok"] and r["adx"] is not None and r["adx"] >= 25)
        print(f"     · {tf}: {len(sub):>4} divergencias → {n1:>4} con volumen → {n2:>3} con ADX≥25")

    # ---- menú de palancas: qué pasa si se saca cada filtro (todas las TF, cooldown 4 h) ----
    filtros_menu = {
        "actual: vol + ADX≥25": lambda r: r["vol_ok"] and r["adx"] is not None and r["adx"] >= 25,
        "sin ADX (solo volumen)": lambda r: r["vol_ok"],
        "sin volumen (solo ADX≥25)": lambda r: r["adx"] is not None and r["adx"] >= 25,
        "ADX≥20 en vez de 25 (con vol)": lambda r: r["vol_ok"] and r["adx"] is not None and r["adx"] >= 20,
        "sin filtros (toda divergencia)": lambda r: True,
    }
    print("\n=== SI QUERÉS MÁS ALERTAS: qué aporta cada filtro (15m+1h+4h+1d, cooldown 4 h) ===")
    hdr = (f"{'configuración':<32} {'alertas/mes':>11} {'n':>5} {'al objetivo':>12} {'mueve/alert':>12}")
    print(hdr)
    print("-" * len(hdr))
    for fname, ff in filtros_menu.items():
        sub = sorted([r for r in rows if ff(r)], key=lambda r: r["t"])
        last, keep = {}, []
        for r in sub:
            if r["t"] - last.get(r["sym"], 0) < B.COOLDOWN_H * 3600_000:
                continue
            last[r["sym"]] = r["t"]
            keep.append(r)
        m = P.stats(keep, FEE, months)
        if not m:
            continue
        out.setdefault("menu_palancas", {})[fname] = {
            "n": m["n"], "sigs_mes": m["sigs_mes"], "winrate": m["winrate"],
            "mov_por_señal_pct": m["mov_por_señal_pct"]}
        print(f"{fname:<32} {m['sigs_mes']:>11} {m['n']:>5} {m['winrate']:>11}% "
              f"{m['mov_por_señal_pct']:>11}%")

    # ---- dos niveles de alerta: premium (vol+ADX) y normal (vol sin ADX) ----
    print("\n=== DOS NIVELES DE ALERTA (con cooldown 4 h, por temporalidad) ===")
    hdr = (f"{'TF':<5} {'nivel':<9} {'alertas/mes':>11} {'n':>5} {'al objetivo':>12} "
           f"{'mueve/alert':>12} {'1ª mitad':>9} {'2ª mitad':>9}")
    print(hdr)
    print("-" * len(hdr))
    for tf in ("15m", "1h", "4h", "1d"):
        sub = [r for r in rows if r["tf"] == tf]
        if len(sub) < 20:
            continue
        for nivel, ff in (("PREMIUM", lambda r: r["vol_ok"] and r["adx"] is not None and r["adx"] >= 25),
                          ("NORMAL", lambda r: r["vol_ok"] and (r["adx"] is None or r["adx"] < 25))):
            sel = sorted([r for r in sub if ff(r)], key=lambda r: r["t"])
            lst, keep = {}, []
            for r in sel:
                if r["t"] - lst.get(r["sym"], 0) < B.COOLDOWN_H * 3600_000:
                    continue
                lst[r["sym"]] = r["t"]
                keep.append(r)
            m = P.stats(keep, FEE, months)
            if not m or m["n"] < 5:
                print(f"{tf:<5} {nivel:<9} {'—':>11} {m['n'] if m else 0:>5}   (muestra insuficiente)")
                continue
            mid = keep[len(keep) // 2]["t"]
            h1 = P.stats([r for r in keep if r["t"] < mid], FEE, months / 2)
            h2 = P.stats([r for r in keep if r["t"] >= mid], FEE, months / 2)
            out.setdefault("tiers", {}).setdefault(tf, {})[nivel.lower()] = {
                "n": m["n"], "sigs_mes": m["sigs_mes"], "winrate": m["winrate"],
                "mov_por_señal_pct": m["mov_por_señal_pct"],
                "acierto_mueve_pct": m["acierto_mueve_pct"], "fallo_mueve_pct": m["fallo_mueve_pct"],
                "mitad_1_mueve": h1["mov_por_señal_pct"] if h1 else None,
                "mitad_2_mueve": h2["mov_por_señal_pct"] if h2 else None}
            print(f"{tf:<5} {nivel:<9} {m['sigs_mes']:>11} {m['n']:>5} {m['winrate']:>11}% "
                  f"{m['mov_por_señal_pct']:>11}% "
                  f"{(h1['mov_por_señal_pct'] if h1 else 0):>8}% {(h2['mov_por_señal_pct'] if h2 else 0):>8}%")

    json.dump(out, open(os.path.join(HERE, "shorts_results.json"), "w"), indent=1)
    print(f"\n# detalle -> {os.path.join(HERE, 'shorts_results.json')}")


if __name__ == "__main__":
    main()
