#!/usr/bin/env python3
"""
Backtest de divergencias RSI en Binance spot (datos publicos, sin API key).

Estrategia evaluada (la que describio Dylan):
  - Watchlist dinamica: top N pares USDT por volumen 24h, sin stablecoins ni tokens apalancados.
  - Indicador: RSI(14) Wilder.
  - Senal: divergencia regular entre los dos ultimos pivotes de swing.
      * alcista : low2 < low1  y  RSI(low2) > RSI(low1) + 2
      * bajista : high2 > high1 y  RSI(high2) < RSI(high1) - 2
  - Filtros de calidad: diferencia de precio entre pivotes >= 0.5%, RSI >= 2 puntos,
    pivotes a <= 60 velas de distancia.
  - Entrada: cierre de la vela que CONFIRMA el pivote (pivote + k velas) -> sin lookahead.
  - Salida: TP = 1.5 x ATR(14), SL = 1.0 x ATR(14), o cierre a las max_bars velas.
  - Comisiones: 0.10% por lado (taker spot) = 0.20% por operacion.

Variantes medidas por temporalidad (15m, 1h, 4h, 1d):
  base            -> solo senal + filtros de calidad
  +vol            -> volumen de la vela de entrada >= 1.2 x SMA20(volumen)
  +tendencia      -> EMA200 del TF superior a favor (4h para 15m/1h, 1d para 4h)
  +cooldown       -> maximo una senal por par cada 4 horas
  completo        -> vol + tendencia + cooldown
"""
import argparse, json, math, os, sys, time
from urllib.request import Request, urlopen
from urllib.error import HTTPError

BASE = "https://api.binance.com"
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache")
os.makedirs(CACHE, exist_ok=True)

MS = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}
PIVOT_K = 3          # pivote = extremo de una ventana de +-3 velas
PIVOT_LOOKBACK = 60  # pivotes comparados a <= 60 velas
MIN_PRICE_DIFF = 0.005
MIN_RSI_DIFF = 2.0
TP_MULT, SL_MULT, FEE = 1.5, 1.0, 0.001
MAX_BARS = {"15m": 100, "1h": 100, "4h": 60, "1d": 30}
COOLDOWN_H = 4
EXCLUDE = ("USDC", "FDUSD", "TUSD", "BUSD", "DAI", "USDP", "EUR", "USDTB", "AEUR", "USD1")
LEV_SUFFIX = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")


def http_json(url, tries=6):
    for a in range(tries):
        try:
            with urlopen(Request(url, headers={"User-Agent": "hermes-backtest/1.0"}), timeout=25) as r:
                return json.loads(r.read().decode())
        except HTTPError as e:
            if e.code in (429, 418, 500, 502, 503, 504):
                time.sleep(2 + a * 3)
                continue
            raise
        except Exception:
            time.sleep(2 + a * 2)
    raise RuntimeError("fallo http: " + url)


def klines(symbol, interval, start_ms, end_ms=None):
    """Velas crudas de Binance, cacheadas en disco."""
    fn = os.path.join(CACHE, f"{symbol}_{interval}_{start_ms}_{end_ms or 'now'}.json")
    if os.path.exists(fn) and os.path.getsize(fn) > 2:
        return json.load(open(fn))
    out, cur = [], start_ms
    while True:
        url = f"{BASE}/api/v3/klines?symbol={symbol}&interval={interval}&startTime={cur}&limit=1000"
        if end_ms:
            url += f"&endTime={end_ms}"
        rows = http_json(url)
        if not rows:
            break
        out += rows
        last = rows[-1][0]
        if len(rows) < 1000 or (end_ms and last >= end_ms):
            break
        cur = last + 1
        time.sleep(0.12)
    json.dump(out, open(fn, "w"))
    return out


def rsi_wilder(closes, period=14):
    n, out = len(closes), [None] * len(closes)
    if n <= period:
        return out
    g = l = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        g += max(d, 0.0)
        l += max(-d, 0.0)
    ag, al = g / period, l / period
    for i in range(period, n):
        if i > period:
            d = closes[i] - closes[i - 1]
            ag = (ag * (period - 1) + max(d, 0.0)) / period
            al = (al * (period - 1) + max(-d, 0.0)) / period
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def atr_wilder(candles, period=14):
    n, out = len(candles), [None] * len(candles)
    if n <= period + 1:
        return out
    trs = []
    for i in range(1, n):
        h, l, pc = float(candles[i][2]), float(candles[i][3]), float(candles[i - 1][4])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    a = sum(trs[:period]) / period
    out[period] = a
    for i in range(period + 1, n):
        a = (a * (period - 1) + trs[i - 1]) / period
        out[i] = a
    return out


def ema(vals, period):
    n, out = len(vals), [None] * len(vals)
    if n < period:
        return out
    a = sum(vals[:period]) / period
    out[period - 1] = a
    k = 2 / (period + 1)
    for i in range(period, n):
        a = vals[i] * k + a * (1 - k)
        out[i] = a
    return out


def sma(vals, period):
    n, out = len(vals), [None] * len(vals)
    s = 0.0
    for i, v in enumerate(vals):
        s += v
        if i >= period:
            s -= vals[i - period]
        if i >= period - 1:
            out[i] = s / period
    return out


def pivots(candles, k=PIVOT_K):
    """Devuelve (idx_pivotes_bajos, idx_pivotes_altos)."""
    lo, hi = [], []
    for i in range(k, len(candles) - k):
        lows = [float(candles[j][3]) for j in range(i - k, i + k + 1)]
        highs = [float(candles[j][2]) for j in range(i - k, i + k + 1)]
        if float(candles[i][3]) == min(lows):
            lo.append(i)
        if float(candles[i][2]) == max(highs):
            hi.append(i)
    return lo, hi


def norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (p, max(0.0, c - h), min(1.0, c + h))


def simulate(candles, e, direction, a, max_bars, tp_mult=TP_MULT, sl_mult=SL_MULT):
    """Devuelve (R_bruto_sin_comisiones, velas, %bruto, riesgo_fraccionado)."""
    entry = float(candles[e][4])
    risk_abs = sl_mult * a
    if risk_abs <= 0 or entry <= 0:
        return None
    if direction == "long":
        sl, tp = entry - risk_abs, entry + tp_mult * a
    else:
        sl, tp = entry + risk_abs, entry - tp_mult * a
    end = min(len(candles), e + 1 + max_bars)
    exit_price, bars = None, 0
    for j in range(e + 1, end):
        h, l = float(candles[j][2]), float(candles[j][3])
        bars = j - e
        hit_sl = l <= sl if direction == "long" else h >= sl
        hit_tp = h >= tp if direction == "long" else l <= tp
        if hit_sl:      # conservador: si toca los dos en la misma vela, cuenta como SL
            exit_price = sl
            break
        if hit_tp:
            exit_price = tp
            break
    if exit_price is None:
        if end - 1 <= e:
            return None
        exit_price = float(candles[end - 1][4])
        bars = end - 1 - e
    gross = (exit_price / entry - 1) if direction == "long" else (entry / exit_price - 1)
    rp = risk_abs / entry                      # riesgo como fraccion del precio
    return gross / rp, bars, 100 * gross, rp


def adx_wilder(candles, period=14):
    """ADX de Wilder con +DI/-DI. Devuelve [(adx, plus_di, minus_di), ...] con None hasta calentar."""
    n = len(candles)
    out = [None] * n
    if n < 2 * period + 2:
        return out
    tr = [0.0] * n
    pdm = [0.0] * n
    mdm = [0.0] * n
    for i in range(1, n):
        h, l, pc = float(candles[i][2]), float(candles[i][3]), float(candles[i - 1][4])
        tr[i] = max(h - l, abs(h - pc), abs(l - pc))
        up = float(candles[i][2]) - float(candles[i - 1][2])
        dn = float(candles[i - 1][3]) - float(candles[i][3])
        pdm[i] = up if (up > dn and up > 0) else 0.0
        mdm[i] = dn if (dn > up and dn > 0) else 0.0
    atr_s = sum(tr[1:period + 1])
    pdm_s = sum(pdm[1:period + 1])
    mdm_s = sum(mdm[1:period + 1])
    adx = None
    dxs = []
    for i in range(period, n):
        if i > period:
            atr_s = atr_s - atr_s / period + tr[i]
            pdm_s = pdm_s - pdm_s / period + pdm[i]
            mdm_s = mdm_s - mdm_s / period + mdm[i]
        if atr_s <= 0:
            continue
        pdi = 100 * pdm_s / atr_s
        mdi = 100 * mdm_s / atr_s
        denom = pdi + mdi
        dx = 100 * abs(pdi - mdi) / denom if denom > 0 else 0.0
        dxs.append(dx)
        if adx is None:
            if len(dxs) >= period:
                adx = sum(dxs[-period:]) / period
            else:
                continue
        else:
            adx = (adx * (period - 1) + dx) / period
        out[i] = (adx, pdi, mdi)
    return out


def obv_slope(candles, bars=20):
    """Pendiente del OBV en las últimas `bars` velas, normalizada por volumen medio.
    Negativo = distribución (ventas dominan); positivo = acumulación."""
    n = len(candles)
    out = [None] * n
    if n < bars + 2:
        return out
    diffs = [0.0] * n
    for i in range(1, n):
        c0, c1 = float(candles[i - 1][4]), float(candles[i][4])
        v = float(candles[i][5])
        diffs[i] = v if c1 > c0 else (-v if c1 < c0 else 0.0)
    run = sum(diffs[1:bars + 1])
    vsum = sum(float(candles[i][5]) for i in range(1, bars + 1))
    for i in range(bars, n):
        if i > bars:
            run += diffs[i] - diffs[i - bars]
            vsum += float(candles[i][5]) - float(candles[i - bars][5])
        out[i] = run / vsum if vsum > 0 else None
    return out


def top_symbols(n):
    d = http_json(f"{BASE}/api/v3/ticker/24hr")
    rows = []
    for x in d:
        s = x["symbol"]
        if not s.endswith("USDT") or s.endswith(LEV_SUFFIX):
            continue
        if s[:-4] in EXCLUDE:
            continue
        rows.append((s, float(x["quoteVolume"])))
    rows.sort(key=lambda r: -r[1])
    return [r[0] for r in rows[:n]]


def analyze(symbol, interval, candles, sigs, lo_ms, hi_ms):
    """
    candles: velas del intervalo.
    sigs   : lista de dicts con idx (pivote), entry_idx, dir, rsi_p, vol_ok, trend_ok
    Filtra por ventana y simula cada senal.
    """
    mb = MAX_BARS[interval]
    open_ms = [int(c[0]) for c in candles]
    out = []
    for s in sigs:
        e = s["entry_idx"]
        if not (lo_ms <= open_ms[e] < hi_ms):
            continue
        a = s["atr"]
        r = simulate(candles, e, s["dir"], a, mb)
        if r is None:
            continue
        R0, bars, pct, rp = r
        out.append({**s, "R0": R0, "bars": bars, "pct": pct, "risk_pct": rp,
                    "sym": symbol, "t": open_ms[e], "interval": interval})
    return out


def build_signals(candles, rsi, atr):
    lo_p, hi_p = pivots(candles)
    vols = [float(c[5]) for c in candles]
    v_sma = sma(vols, 20)
    sigs = []
    for piv, direction in ((lo_p, "long"), (hi_p, "short")):
        for i in range(1, len(piv)):
            p1, p2 = piv[i - 1], piv[i]
            if p2 - p1 > PIVOT_LOOKBACK or p2 - p1 < PIVOT_K * 2:
                continue
            if rsi[p1] is None or rsi[p2] is None:
                continue
            entry_idx = p2 + PIVOT_K
            if entry_idx >= len(candles):
                continue
            if direction == "long":
                v1, v2 = float(candles[p1][3]), float(candles[p2][3])
                if not (v2 < v1 and (v1 - v2) / v1 >= MIN_PRICE_DIFF):
                    continue
                if not (rsi[p2] > rsi[p1] + MIN_RSI_DIFF):
                    continue
            else:
                v1, v2 = float(candles[p1][2]), float(candles[p2][2])
                if not (v2 > v1 and (v2 - v1) / v1 >= MIN_PRICE_DIFF):
                    continue
                if not (rsi[p2] < rsi[p1] - MIN_RSI_DIFF):
                    continue
            if atr[entry_idx] is None or rsi[p2] is None:
                continue
            vol_ok = None not in (v_sma[entry_idx],) and \
                float(candles[entry_idx][5]) >= 1.2 * v_sma[entry_idx]
            sigs.append({"idx": p2, "p1_idx": p1, "entry_idx": entry_idx, "dir": direction,
                         "rsi_p": rsi[p2], "atr": atr[entry_idx],
                         "vol_ok": bool(vol_ok), "trend_ok": None, "t": int(candles[entry_idx][0])})
    sigs.sort(key=lambda s: s["entry_idx"])
    return sigs


def apply_trend(sigs, higher_candles, need_ema=200):
    """Marca trend_ok segun EMA200 del TF superior (sin lookahead: solo velas cerradas)."""
    if not higher_candles:
        return
    closes = [float(c[4]) for c in higher_candles]
    emas = ema(closes, need_ema)
    ct = [int(c[6]) for c in higher_candles]  # closeTime
    for s in sigs:
        t = s["t"]
        j = None
        lo, hi = 0, len(ct) - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            if ct[mid] <= t:
                j = mid
                lo = mid + 1
            else:
                hi = mid - 1
        if j is None or emas[j] is None:
            s["trend_ok"] = False
            continue
        px = float(higher_candles[j][4])
        s["trend_ok"] = (px > emas[j]) if s["dir"] == "long" else (px < emas[j])


def apply_cooldown(sigs, interval):
    """Deja solo la primera senal de cada par por ventana de COOLDOWN_H horas."""
    win = COOLDOWN_H * 3600_000
    last = {}
    keep = []
    for s in sorted(sigs, key=lambda x: x["t"]):
        key = s["sym"]
        if key in last and s["t"] - last[key] < win:
            s["cooldown_ok"] = False
        else:
            s["cooldown_ok"] = True
            last[key] = s["t"]
        keep.append(s)
    return keep


def metrics(rows, fee):
    """Metricas netas aplicando comisiones: R = R_bruto - 2*fee/riesgo_fraccionado."""
    n = len(rows)
    if n == 0:
        return {"n": 0}
    Rs = [r["R0"] - 2 * fee / r["risk_pct"] for r in rows]
    wins = [x for x in Rs if x > 0]
    p, ci_lo, ci_hi = wilson(len(wins), n)
    gp = sum(wins)
    gl = -sum(x for x in Rs if x <= 0)
    return {
        "n": n,
        "winrate": round(100 * p, 1),
        "ic95": [round(100 * ci_lo, 1), round(100 * ci_hi, 1)],
        "exp_R": round(sum(Rs) / n, 3),
        "total_R": round(sum(Rs), 1),
        "avg_win_R": round(gp / len(wins), 2) if wins else 0,
        "avg_loss_R": round(-gl / (n - len(wins)), 2) if n - len(wins) else 0,
        "profit_factor": round(gp / gl, 2) if gl > 0 else None,
        "avg_bars": round(sum(r["bars"] for r in rows) / n, 1),
        "avg_risk_pct": round(100 * sum(r["risk_pct"] for r in rows) / n, 2),
        "drag_R_por_trade": round(2 * fee / (sum(r["risk_pct"] for r in rows) / n), 3),
    }


def baseline(candles, interval, lo_ms, hi_ms, fee, step=25):
    """Tasa base de la estructura TP1.5/SL1 sin ninguna senal: entradas ciegas cada 'step' velas."""
    mb = MAX_BARS[interval]
    atr = atr_wilder(candles)
    rows = []
    i = 300
    d = "long"
    while i < len(candles) - mb:
        if lo_ms <= int(candles[i][0]) < hi_ms and atr[i]:
            r = simulate(candles, i, d, atr[i], mb)
            if r:
                rows.append({"R0": r[0], "bars": r[1], "risk_pct": r[3]})
        d = "short" if d == "long" else "long"
        i += step
    return metrics(rows, fee)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="", help="lista separada por comas; vacio = top por volumen")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--intervals", default="15m,1h,4h,1d")
    ap.add_argument("--out", default=os.path.join(HERE, "backtest_results.json"))
    ap.add_argument("--fees", default="0.001,0.0005,0.0002",
                    help="comision por lado, en fraccion: 0.001=spot taker 0.10%%, 0.0005=futuros taker, 0.0002=futuros maker")
    args = ap.parse_args()

    syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or top_symbols(args.top)
    intervals = args.intervals.split(",")
    now = int(time.time() * 1000)
    hi = (now // 86_400_000) * 86_400_000   # fijado al dia UTC: mantiene el cache valido
    lo = hi - args.days * 86_400_000
    warm = {i: max(300, 60) * MS[i] for i in intervals}
    warm["1d"] = 400 * MS["1d"]

    print(f"# universo: {syms}", file=sys.stderr)
    print(f"# ventana: {args.days}d  {time.strftime('%Y-%m-%d', time.gmtime(lo/1000))} -> "
          f"{time.strftime('%Y-%m-%d', time.gmtime(hi/1000))}", file=sys.stderr)

    all_rows, base_rows, coverage = [], {}, {}
    for sym in syms:
        data = {}
        try:
            for iv in set(intervals + ["4h", "1d"]):
                start = lo - warm.get(iv, 300 * MS[iv])
                data[iv] = klines(sym, iv, start, hi)
                print(f"  {sym} {iv}: {len(data[iv])} velas", file=sys.stderr)
                time.sleep(0.05)
        except Exception as e:
            print(f"!! {sym}: {e}", file=sys.stderr)
            continue
        exp15 = args.days * 86400_000 / MS["15m"]
        coverage[sym] = round(100 * len(data["15m"]) / exp15, 1)
        for iv in intervals:
            c = data[iv]
            if len(c) < 400:
                continue
            closes = [float(x[4]) for x in c]
            rsi = rsi_wilder(closes)
            atr = atr_wilder(c)
            sigs = build_signals(c, rsi, atr)
            for s in sigs:
                s["sym"] = sym
            higher = data["4h"] if iv in ("15m", "1h") else (data["1d"] if iv == "4h" else None)
            if higher:
                apply_trend(sigs, higher)
            else:
                for s in sigs:
                    s["trend_ok"] = True
            rows = analyze(sym, iv, c, sigs, lo, hi)
            all_rows += rows
            base_rows.setdefault(iv, []).append(c)
        print(f"  -> {sym} listo", file=sys.stderr)

    all_rows = apply_cooldown(all_rows, None)
    FEES = [float(x) for x in args.fees.split(",")]

    report = {"meta": {"generated": time.strftime("%Y-%m-%d %H:%M:%S %z"),
                       "symbols": syms, "days": args.days,
                       "coverage_pct": coverage,
                       "tp_mult": TP_MULT, "sl_mult": SL_MULT,
                       "fees_pct_side": [f * 100 for f in FEES],
                       "breakeven_winrate_pct": round(100 / (1 + TP_MULT), 1)},
              "baseline": {}, "by_interval": {}}

    for iv, cds in base_rows.items():
        report["baseline"][iv] = {}
        for fee in FEES:
            b = []
            for c in cds:
                try:
                    b.append(baseline(c, iv, lo, hi, fee))
                except Exception as e:
                    print(f"!! baseline {iv}: {e}", file=sys.stderr)
            n = sum(x["n"] for x in b if x["n"])
            if not n:
                continue
            wr = sum(x["winrate"] * x["n"] / 100 for x in b if x["n"]) / n * 100
            report["baseline"][iv][f"{fee*100:.2f}%"] = {
                "n": n, "winrate": round(wr, 1),
                "exp_R": round(sum(x["exp_R"] * x["n"] for x in b if x["n"]) / n, 3),
                "avg_risk_pct": round(sum(x["avg_risk_pct"] * x["n"] for x in b if x["n"]) / n, 2)}

    gates = {
        "base": lambda r: True,
        "base+vol": lambda r: r["vol_ok"],
        "base+tendencia": lambda r: r["trend_ok"],
        "base+vol+tendencia": lambda r: r["vol_ok"] and r["trend_ok"],
        "base+cooldown": lambda r: r["cooldown_ok"],
        "completo(vol+tend+cooldown)": lambda r: r["vol_ok"] and r["trend_ok"] and r["cooldown_ok"],
    }
    months = args.days / 30.4
    for iv in intervals:
        rows = [r for r in all_rows if r["interval"] == iv]
        if not rows:
            continue
        report["by_interval"][iv] = {}
        for name, f in gates.items():
            sub = [r for r in rows if f(r)]
            cell = {"n": len(sub)}
            if sub:
                longs = [r for r in sub if r["dir"] == "long"]
                shorts = [r for r in sub if r["dir"] == "short"]
                for fee in FEES:
                    m = metrics(sub, fee)
                    m["sigs_par_mes"] = round(m["n"] / max(1, len(coverage)) / months, 2)
                    net = lambda r: r["R0"] - 2 * fee / r["risk_pct"]
                    m["winrate_long"] = round(100 * sum(1 for r in longs if net(r) > 0) / len(longs), 1) if longs else None
                    m["winrate_short"] = round(100 * sum(1 for r in shorts if net(r) > 0) / len(shorts), 1) if shorts else None
                    cell[f"{fee*100:.2f}%"] = m
            report["by_interval"][iv][name] = cell

    json.dump(report, open(args.out, "w"), indent=1)

    # ---- salida legible ----
    be = report["meta"]["breakeven_winrate_pct"]
    print(f"\n=== TASA BASE por temporalidad (entradas ciegas, misma estructura TP1.5/SL1) ===")
    print(f"# winrate de equilibrio teorico (TP 1.5R / SL 1R, sin comisiones): {be}%")
    for iv in intervals:
        for fk, b in (report["baseline"].get(iv) or {}).items():
            print(f"  {iv:>4} comision {fk}/lado: n={b['n']:<6} winrate={b['winrate']:>5}%  "
                  f"expR={b['exp_R']:>7}  riesgo_medio={b['avg_risk_pct']}%")

    for fee in FEES:
        fk = f"{fee*100:.2f}%"
        print(f"\n=== SEÑALES — comision {fk} por lado ===")
        hdr = (f"{'TF':>4} {'variante':<28} {'n':>5} {'win%':>6} {'IC95':>13} {'expR':>7} "
               f"{'totR':>7} {'PF':>5} {'dragR':>6} {'riesgo%':>7} {'sig/par/mes':>11}")
        print(hdr)
        print("-" * len(hdr))
        for iv in intervals:
            for name, cell in (report["by_interval"].get(iv) or {}).items():
                m = cell.get(fk)
                if not m or not m.get("n"):
                    print(f"{iv:>4} {name:<28} {'-- sin senales --':>40}")
                    continue
                ic = f"[{m['ic95'][0]}-{m['ic95'][1]}]"
                pf = m["profit_factor"] if m["profit_factor"] is not None else "inf"
                print(f"{iv:>4} {name:<28} {m['n']:>5} {m['winrate']:>6} {ic:>13} {m['exp_R']:>7} "
                      f"{m['total_R']:>7} {str(pf):>5} {m['drag_R_por_trade']:>6} "
                      f"{m['avg_risk_pct']:>7} {m['sigs_par_mes']:>11}")
    print(f"\n# resultados completos -> {args.out}")


if __name__ == "__main__":
    main()
