#!/usr/bin/env python3
"""VERIFICACIÓN DE DATOS: ¿las velas que usamos son las mismas que se ven en TradingView?

No se puede leer TradingView por API, pero sí comparar nuestra fuente (Binance spot) contra:
  · el otro endpoint de Binance (`/api/v3/uiKlines`, distinto código/caché)
  · otro exchange (Bybit) para el mismo par y temporalidad
Si las tres coinciden vela por vela, el dato crudo es correcto y cualquier diferencia visual es de
dibujo (escala, sin eje de tiempo, volumen dentro del panel), no de datos.
"""
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import signals as S

SIMBOLOS = [("SUIUSDT", "15m"), ("BTCUSDT", "4h"), ("SOLUSDT", "1h")]


def pedir(url):
    r = urllib.request.Request(url, headers={"User-Agent": "hermes-verifica/1"})
    with urllib.request.urlopen(r, timeout=25) as x:
        return json.loads(x.read().decode())


def binance_spot(sym, tf, limite=30):
    d = pedir(f"https://api.binance.com/api/v3/klines?symbol={sym}&interval={tf}&limit={limite}")
    return {int(k[0]): (float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])) for k in d}


def binance_uiklines(sym, tf, limite=30):
    d = pedir(f"https://api.binance.com/api/v3/uiKlines?symbol={sym}&interval={tf}&limit={limite}")
    return {int(k[0]): (float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])) for k in d}


def bybit(sym, tf, limite=30):
    d = pedir(f"https://api.bybit.com/v5/market/kline?category=spot&symbol={sym}&interval="
              f"{ {'1h':'60','4h':'240','15m':'15'}[tf] }&limit={limite}")
    filas = d.get("result", {}).get("list", [])
    # bybit: [start, open, high, low, close, volume, turnover] y viene del más nuevo al más viejo
    return {int(f[0]): (float(f[1]), float(f[2]), float(f[3]), float(f[4]), float(f[5]))
            for f in filas}


for sym, tf in SIMBOLOS:
    print(f"\n=== {sym} {tf} · comparación vela por vela ===")
    a = binance_spot(sym, tf)
    b = binance_uiklines(sym, tf)
    comunes = sorted(set(a) & set(b))[:-1]          # la última está viva: se excluye
    dif = [t for t in comunes if max(abs(a[t][i] - b[t][i]) for i in range(4)) > 1e-9]
    print(f"  Binance klines vs Binance uiKlines: {len(comunes)} velas cerradas · "
          f"diferencias exactas: {len(dif)}" + ("  ✅ idénticas" if not dif else f"  ❌ {dif[:3]}"))
    try:
        c = bybit(sym, tf)
        comunes2 = sorted(set(a) & set(c))[:-1]
        if comunes2:
            difs = []
            for t in comunes2:
                rel = max(abs(a[t][i] - c[t][i]) / max(a[t][i], 1e-9) for i in range(4))
                if rel > 0.002:                      # tolerancia 0.2% entre exchanges distintos
                    difs.append((t, rel))
            peor = max((abs(a[t][i] - c[t][i]) / max(a[t][i], 1e-9)
                        for t in comunes2 for i in range(4)), default=0)
            print(f"  Binance vs Bybit (otro exchange): {len(comunes2)} velas en común · "
                  f"diferencia máxima {peor * 100:.3f}%" +
                  ("  ✅ compatibles" if not difs else f"  ⚠️ {len(difs)} velas >0.2%"))
        else:
            print("  Binance vs Bybit: sin horarios en común (revisar símbolo)")
    except Exception as e:
        print(f"  Bybit no disponible: {type(e).__name__}")
    # muestra de velas para comparar a ojo contra TradingView
    ks = sorted(comunes)[-3:]
    print("  últimas 3 velas cerradas (hora UTC · open/high/low/close/volumen):")
    for t in ks:
        o, h, l, cl, v = a[t]
        print(f"    {time.strftime('%Y-%m-%d %H:%M', time.gmtime(t / 1000))} · "
              f"{o:.6g} / {h:.6g} / {l:.6g} / {cl:.6g} / {v:.0f}")

print("\n=== qué usa el bot ===")
print("  fuente: Binance SPOT (api.binance.com), velas cerradas, límite 300, intervalos 15m/1h/4h/1d")
print("  los horarios son UTC (TradingView los muestra en TU zona horaria: eso corre las velas de lugar)")
print("  TradingView por defecto puede mostrar otro mercado del mismo par: BINANCE:SUIUSDT (spot) vs")
print("  BINANCE:SUIUSDT.P (perpetuo) o BYBIT:* — los precios del perpetuo difieren unos puntos base")
