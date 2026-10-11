#!/usr/bin/env python3
"""SONDA DE FUENTES DE DATOS desde el runner: ¿alguna sirve velas a una IP de EE.UU.?

El problema detectado: `api.binance.com` devuelve **HTTP 451** al runner de GitHub (IP de EE.UU.:
Binance.com no sirve a IPs estadounidenses). Acá se prueban las alternativas para ver si alguna
entrega las MISMAS velas: hosts alternativos de Binance, el endpoint público de datos
(`data-api.binance.vision`), Binance US y otros exchanges que sí operan en EE.UU.

Escribe el resultado en sonda_fuentes.txt (lo commitea el workflow) y lo imprime.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(RAIZ)

FUENTES = [
    ("Binance api.binance.com", "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1h&limit=3"),
    ("Binance api1.binance.com", "https://api1.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1h&limit=3"),
    ("Binance api2.binance.com", "https://api2.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1h&limit=3"),
    ("Binance api4.binance.com", "https://api4.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1h&limit=3"),
    ("Binance data-api.binance.vision", "https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT&interval=1h&limit=3"),
    ("Binance data-api (futuros)", "https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=1h&limit=3"),
    ("Binance US (api.binance.us)", "https://api.binance.us/api/v3/klines?symbol=BTCUSDT&interval=1h&limit=3"),
    ("Coinbase Exchange", "https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=3600"),
    ("Kraken", "https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval=60"),
    ("Bybit", "https://api.bybit.com/v5/market/kline?category=spot&symbol=BTCUSDT&interval=60&limit=3"),
    ("OKX", "https://www.okx.com/api/v5/market/candles?instId=BTC-USDT&bar=1H&limit=3"),
    ("Bitstamp", "https://www.bitstamp.net/api/v2/ohlc/btcusd/?step=3600&limit=3"),
    ("Gate.io", "https://api.gateio.ws/api/v4/spot/candlesticks?currency_pair=BTC_USDT&interval=1h&limit=3"),
    ("KuCoin", "https://api.kucoin.com/api/v1/market/candles?type=1hour&symbol=BTC-USDT"),
    ("MEXC", "https://api.mexc.com/api/v3/klines?symbol=BTCUSDT&interval=60m&limit=3"),
]

lineas = []
print("=== SONDA DE FUENTES DE DATOS desde el runner ===", flush=True)
for nombre, url in FUENTES:
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "hermes-sonda/1"})
        with urllib.request.urlopen(req, timeout=20) as r:
            cuerpo = r.read()
            # ¿trae velas de verdad? (al menos un arreglo con números)
            trae = len(cuerpo) > 20 and (b"[" in cuerpo or b"data" in cuerpo)
            linea = (f"✅ {nombre}: HTTP {r.status} · {len(cuerpo)} bytes · "
                     f"{(time.time() - t0) * 1000:.0f} ms · datos {'sí' if trae else 'NO'}")
    except urllib.error.HTTPError as e:
        linea = f"❌ {nombre}: HTTP {e.code} ({(time.time() - t0) * 1000:.0f} ms)"
    except Exception as e:
        linea = f"❌ {nombre}: {type(e).__name__} ({(time.time() - t0) * 1000:.0f} ms)"
    print("  " + linea, flush=True)
    lineas.append(linea)

resumen = ("🔬 **Sonda de fuentes de datos desde GitHub Actions**\n" +
           "\n".join("• " + l for l in lineas) +
           "\nℹ️ Necesitamos una fuente que entregue velas a una IP de EE.UU. (Binance.com da 451). "
           "Si alguna ✅ trae datos, el bot puede correr en GitHub cambiando solo la URL base.")
try:
    with open(os.path.join(RAIZ, "sonda_fuentes.txt"), "w") as fh:
        fh.write(time.strftime("%Y-%m-%d %H:%M:%S UTC\n", time.gmtime()))
        fh.write("\n".join(lineas) + "\n")
except Exception as e:
    print("  no se pudo escribir el archivo:", type(e).__name__, flush=True)
print("\n" + resumen, flush=True)
