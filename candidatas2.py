#!/usr/bin/env python3
"""¿Hay algún indicador/filtro NUEVO que mejore las señales de short? Se mide, no se opina.

Candidatos NUNCA probados en este proyecto (los ya probados y descartados viven en NOTAS.md:
divergencia oculta, confluencia S/R, RSI>70, RSI(9), EMA200 del TF superior, BTC bajista → 0 de 7):
  1. hora del día (sesión asiática / europea / americana)
  2. percentil de ATR del propio par (volatilidad RELATIVA, no el ATR absoluto)
  3. volumen en dólares de la vela de entrada (liquidez real, no el volumen relativo)
  4. día de la semana (laborable vs fin de semana)
  5. atribución: qué hizo BTC en la misma ventana (¿la señal aporta o solo sigue al mercado?)

Mismas reglas que el resto del proyecto: señales de short con volumen ≥1.2× (la puerta del bot),
salida 2×ATR / 1×ATR, comisión 0.10%/lado, empate = stop. Se exige que el efecto aguante las 3
ventanas de 1 año: es la única forma de no confundir suerte con ventaja.
"""
import glob
import json
import os
import statistics
import sys

RAIZ = os.path.dirname(os.path.abspath(__file__))
if os.path.basename(RAIZ) == "dev":
    RAIZ = os.path.dirname(RAIZ)          # los scripts de dev/ viven un nivel abajo
sys.path.insert(0, RAIZ)
os.chdir(RAIZ)
import backtest as B

CACHE = "cache"
FEE = 0.001
TP, SL, VOL_MULT = 2.0, 1.0, 1.2
VENTANA = 365 * 86_400_000


def cargar(sym, iv):
    patrones = sorted(glob.glob(os.path.join(CACHE, f"{sym}_{iv}_*.json")))
    mejor = None
    for p in patrones:
        try:
            k = json.load(open(p))
        except Exception:
            continue
        if isinstance(k, list) and len(k) > 800 and (mejor is None or len(k) > len(mejor)):
            mejor = k
    return mejor


def señales(c, iv, sym):
    """Extrae las señales de short igual que el bot y calcula el resultado + los candidatos."""
    closes = [float(x[4]) for x in c]
    vols = [float(x[5]) for x in c]
    rsi = B.rsi_wilder(closes)
    atr = B.atr_wilder(c)
    adx = B.adx_wilder(c)
    v_sma = B.sma(vols, 20)
    lo_p, hi_p = B.pivots(c)
    salida = []
    for i in range(1, len(hi_p)):
        p1, p2 = hi_p[i - 1], hi_p[i]
        if p2 - p1 > B.PIVOT_LOOKBACK or p2 - p1 < B.PIVOT_K * 2:
            continue
        e = p2 + B.PIVOT_K
        if e >= len(c) - 1 or rsi[p1] is None or rsi[p2] is None or atr[e] is None or not v_sma[e]:
            continue
        v1, v2 = float(c[p1][2]), float(c[p2][2])
        if not (v2 > v1 and (v2 - v1) / v1 >= B.MIN_PRICE_DIFF):
            continue
        if not (rsi[p2] < rsi[p1] - B.MIN_RSI_DIFF):
            continue
        vol_ratio = vols[e] / v_sma[e]
        if vol_ratio < VOL_MULT:
            continue
        r = B.simulate(c, e, "short", atr[e], B.MAX_BARS[iv], TP, SL)
        if r is None:
            continue
        R0, bars, _g, rp = r
        mov = (R0 - 2 * FEE / rp) * rp * 100
        # --- candidatos nuevos
        atr_hist = [100 * atr[j] / closes[j] for j in range(max(20, e - 400), e)
                    if atr[j] and closes[j]]
        pct_atr = (sum(1 for v in atr_hist if v < 100 * atr[e] / closes[e]) / len(atr_hist)
                   if atr_hist else 0.5)
        dolares = float(c[e][7]) if len(c[e]) > 7 else vols[e] * closes[e]   # quoteVolume
        hora = (int(c[e][0]) // 3_600_000) % 24
        dia = (int(c[e][0]) // 86_400_000 + 4) % 7          # 0 = lunes (epoch era jueves)
        salida.append({"sym": sym, "tf": iv, "t": int(c[e][0]), "mov": mov,
                       "atr_pct": 100 * rp, "mov_flotante": mov,
                       "pct_atr": pct_atr, "dolares": dolares, "hora": hora, "dia": dia})
    return salida


def btc_en_ventana(c_btc, ts, iv, bars):
    """% que hizo BTC en la misma ventana (desde la vela de entrada hacia adelante)."""
    idx = next((i for i, x in enumerate(c_btc) if int(x[0]) == ts), None)
    if idx is None or idx + 1 >= len(c_btc):
        return None
    ini = float(c_btc[idx][4])
    fin = float(c_btc[min(idx + bars, len(c_btc) - 1)][4])
    return (fin / ini - 1) * 100


def tercera(x):
    return {"bajo": x <= 1 / 3, "medio": 1 / 3 < x <= 2 / 3, "alto": x > 2 / 3}


def evaluar(nombre, filas, clave):
    """% de movimiento medio por señal en cada tramo, en las 3 ventanas de 1 año."""
    print(f"\n--- {nombre} ---")
    t0 = min(f["t"] for f in filas)
    tramos = sorted({clave(f) for f in filas})
    cabecera = f"  {'tramo':<26} {'n':>5} {'%/señal':>9}   ventanas (1ª/2ª/3ª)"
    print(cabecera)
    for tr in tramos:
        sub = [f for f in filas if clave(f) == tr]
        if len(sub) < 25:
            continue
        mov = statistics.fmean(f["mov"] for f in sub)
        vent = []
        for w in range(3):
            vs = [f for f in sub if t0 + w * VENTANA <= f["t"] < t0 + (w + 1) * VENTANA]
            vent.append(statistics.fmean(f["mov"] for f in vs) if len(vs) >= 8 else None)
        todas_pos = all(v is not None and v > 0 for v in vent)
        marca = "  ← aguanta las 3" if todas_pos else ""
        print(f"  {str(tr):<26} {len(sub):>5} {mov:>+8.2f}%   "
              + " / ".join(f"{v:+.2f}%" if v is not None else "s/d" for v in vent) + marca)


def main():
    prev = json.load(open("backtest_results.json"))
    syms = prev["meta"]["symbols"]
    filas = []
    for sym in syms:
        for iv in ("4h", "1h"):
            c = cargar(sym, iv)
            if not c:
                continue
            ss = señales(c, iv, sym)
            cbtc = cargar("BTCUSDT", iv)
            if cbtc and ss:
                for s in ss:
                    s["btc"] = btc_en_ventana(cbtc, s["t"], iv, B.MAX_BARS[iv])
            filas += ss
    print(f"señales de short con volumen medidas: {len(filas)} "
          f"({len(syms)} pares, 4h y 1h, ~3 años)")
    if len(filas) < 80:
        print("muestra insuficiente"); return
    base = statistics.fmean(f["mov"] for f in filas)
    print(f"referencia (todas juntas): {base:+.2f}% por señal")

    def sesion(f):
        h = f["hora"]
        return "Asia (0-7 UTC)" if h < 8 else "Europa (8-15 UTC)" if h < 16 else "América (16-23 UTC)"

    evaluar("1) Hora del día", filas, sesion)
    evaluar("2) Percentil de ATR del par (volatilidad relativa)", filas,
            lambda f: ("ATR bajo (≤33%)" if f["pct_atr"] <= 1 / 3 else
                       "ATR medio" if f["pct_atr"] <= 2 / 3 else "ATR alto (>66%)"))
    evaluar("3) Volumen en dólares de la vela (>1M / >10M)", filas,
            lambda f: ("<1 M" if f["dolares"] < 1e6 else "1-10 M" if f["dolares"] < 1e7 else ">10 M"))
    evaluar("4) Día de la semana", filas,
            lambda f: "Fin de semana" if f["dia"] in (5, 6) else "Laborable")
    con_btc = [f for f in filas if f.get("btc") is not None]
    evaluar("5) BTC en la misma ventana (atribución)", con_btc,
            lambda f: ("BTC subió >1%" if f["btc"] > 1 else
                       "BTC bajó >1%" if f["btc"] < -1 else "BTC lateral"))
    json.dump(filas, open("candidatas2_results.json", "w"), indent=1)
    print("\nguardado en candidatas2_results.json")


main()
