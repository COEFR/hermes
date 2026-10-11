#!/usr/bin/env python3
"""
Candidatas para MEJORAR el lado SHORT, medidas con el control de 3 ventanas anuales.

Todo lo que sabemos del bot es del lado short (es solo-short). Se prueban sobre los mismos 3 años:

  clasica      — divergencia bajista clásica (precio máximo más alto + RSI más bajo)   [lo actual]
  oculta       — divergencia OCULTA bajista (precio máximo más BAJO + RSI más ALTO)
                 -> señal de continuidad: opera CON la tendencia de baja
  confluencia  — la clásica, pero el entry está pegado (<=1%) a una resistencia real
  extremo      — la clásica, pero el primer pivote tenía RSI > 70 ("setup clase A")
  rsi9         — la misma detección clásica con RSI de 9 períodos en vez de 14

Todas exigen el filtro de volumen del bot (>=1.2x la media de 20) y salida TP 2xATR / SL 1xATR.
Una variante sirve solo si queda positiva en LAS TRES ventanas.
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
TP, SL = 2.0, 1.0
VARIANTES = ("clasica", "oculta", "confluencia", "extremo", "rsi9",
             "cl_bajista", "cl_btc_bajista")


def mapa_ema_diaria(candles_1d):
    """[(closeTime, ema200, cierre)] de la serie diaria, para saber si el activo estaba bajista."""
    closes = [float(x[4]) for x in candles_1d]
    emas = B.ema(closes, 200)
    return [(int(candles_1d[i][6]), emas[i], closes[i]) for i in range(len(candles_1d))
            if emas[i] is not None]


def bajista_en(mapa, t):
    """¿el cierre diario anterior a t estaba por DEBAJO de su EMA200? (tendencia bajista)"""
    estado = None
    for ct, ema, cierre in mapa:
        if ct > t:
            break
        estado = cierre < ema
    return estado


def main():
    prev = json.load(open(os.path.join(HERE, "backtest_results.json")))
    syms = prev["meta"]["symbols"]
    now = int(time.time() * 1000)
    hi_glob = (now // DIA) * DIA
    lo_glob = hi_glob - 1095 * DIA
    warm = {iv: max(300, 60) * B.MS[iv] for iv in ("15m", "1h", "4h", "1d")}
    warm["1d"] = 400 * B.MS["1d"]
    btc_1d = mapa_ema_diaria(B.klines("BTCUSDT", "1d", lo_glob - warm["1d"], hi_glob))

    filas = {v: [] for v in VARIANTES}
    for sym in syms:
        data = {iv: B.klines(sym, iv, lo_glob - warm[iv], hi_glob) for iv in ("15m", "1h", "4h", "1d")}
        for iv in ("15m", "1h", "4h", "1d"):
            c = data[iv]
            if len(c) < 400:
                continue
            closes = [float(x[4]) for x in c]
            vols = [float(x[5]) for x in c]
            r14, r9 = B.rsi_wilder(closes), B.rsi_wilder(closes, 9)
            atr, v_sma = B.atr_wilder(c), B.sma(vols, 20)
            lo_p, hi_p = B.pivots(c)
            mb = B.MAX_BARS[iv]
            mapa_1d = mapa_ema_diaria(data["1d"])
            for i in range(1, len(hi_p)):
                p1, p2 = hi_p[i - 1], hi_p[i]
                if p2 - p1 > B.PIVOT_LOOKBACK or p2 - p1 < B.PIVOT_K * 2:
                    continue
                e = p2 + B.PIVOT_K
                if not (lo_glob <= int(c[e][0]) < hi_glob) or atr[e] is None or v_sma[e] is None:
                    continue
                if (vols[e] / v_sma[e]) < VOL_MULT:          # puerta de volumen, igual que el bot
                    continue
                if r14[p1] is None or r14[p2] is None:
                    continue
                v1, v2 = float(c[p1][2]), float(c[p2][2])
                entry = closes[e]
                sim = B.simulate(c, e, "short", atr[e], mb, TP, SL)
                if sim is None:
                    continue
                base = {"t": int(c[e][0]), "sym": sym, "tf": iv, "R0": sim[0], "risk_pct": sim[3]}
                clasica = (v2 > v1 and (v2 - v1) / v1 >= B.MIN_PRICE_DIFF
                           and r14[p2] < r14[p1] - B.MIN_RSI_DIFF)
                oculta = (v2 < v1 and (v1 - v2) / v1 >= B.MIN_PRICE_DIFF
                          and r14[p2] > r14[p1] + B.MIN_RSI_DIFF)
                rsi9s = (r9[p1] is not None and r9[p2] is not None and v2 > v1
                         and (v2 - v1) / v1 >= B.MIN_PRICE_DIFF and r9[p2] < r9[p1] - B.MIN_RSI_DIFF)
                if clasica:
                    filas["clasica"].append(dict(base))
                    if any(entry < float(c[j][2]) <= entry * 1.01 for j in hi_p):
                        filas["confluencia"].append(dict(base))
                    if r14[p1] > 70:
                        filas["extremo"].append(dict(base))
                    if bajista_en(mapa_1d, int(c[e][0])):
                        filas["cl_bajista"].append(dict(base))
                    if bajista_en(btc_1d, int(c[e][0])):
                        filas["cl_btc_bajista"].append(dict(base))
                if oculta:
                    filas["oculta"].append(dict(base))
                if rsi9s:
                    filas["rsi9"].append(dict(base))
        print(f"  {sym} listo", file=sys.stderr)

    print("\n=== CANDIDATAS DEL LADO SHORT (3 años en 3 ventanas · volumen ≥1.2× · TP2×ATR/SL1×ATR) ===")
    print("# cada celda: % de movimiento por alerta y n; sirve solo si es positiva en las TRES ventanas")
    hdr = (f"{'variante':<14} {'n tot':>6} " + " ".join(f"{'ventana ' + str(i + 1):>17}" for i in range(3))
           + f" {'¿3/3?':>7}")
    print(hdr)
    print("-" * len(hdr))
    resumen = {}
    for nombre in VARIANTES:
        rows = filas[nombre]
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
            resumen.setdefault(nombre, {})[f"ventana{w+1}"] = {
                "n": m["n"], "winrate": m["winrate"], "mov": m["mov_por_señal_pct"]}
        ok = validas == 3 and positivas == 3
        resumen.setdefault(nombre, {})["positiva_en_3"] = ok
        print(f"{nombre:<14} {len(rows):>6} " + " ".join(celdas) + f" {('SÍ' if ok else 'no'):>7}")

    json.dump(resumen, open(os.path.join(HERE, "candidatas_results.json"), "w"), indent=1)
    print(f"\n# detalle -> {os.path.join(HERE, 'candidatas_results.json')}")


if __name__ == "__main__":
    main()
