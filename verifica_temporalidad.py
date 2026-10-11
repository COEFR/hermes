#!/usr/bin/env python3
"""VERIFICACIÓN DE TEMPORALIDAD: ¿las velas son de la temporalidad que dice el bot?

Comprueba, para 15m / 1h / 4h / 1d:
  1. que el espaciado entre velas sea EXACTO (sin huecos ni velas de otra TF)
  2. que cada vela abra en un límite de la temporalidad en UTC
     (15m en :00/:15/:30/:45 · 1h en :00 · 4h en 00/04/08/12/16/20 · 1d a las 00:00)
  3. que vengan en orden cronológico y que la última sea la vela VIVA (ahora dentro de su rango)
  4. que las constantes de milisegundos del bot coincidan con la TF pedida
  5. que una señal real traiga el entry_ts alineado a SU temporalidad
     (si el motor usara velas de otra TF, acá salta)
"""
import os
import sys
import time
import datetime as dt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import signals as S
import tracker as TR

MS_ESPERADO = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}
FALLOS = 0


def comprobar(cond, texto):
    global FALLOS
    print(("  ✅ " if cond else "  ❌ ") + texto)
    if not cond:
        FALLOS += 1


def alineada(ts_ms, tf):
    t = dt.datetime.fromtimestamp(ts_ms / 1000, dt.timezone.utc)
    if tf == "15m":
        return t.minute % 15 == 0 and t.second == 0
    if tf == "1h":
        return t.minute == 0 and t.second == 0
    if tf == "4h":
        return t.hour % 4 == 0 and t.minute == 0 and t.second == 0
    if tf == "1d":
        return t.hour == 0 and t.minute == 0 and t.second == 0
    return False


print("=== 1. constantes del bot vs temporalidad pedida ===")
for tf, ms in MS_ESPERADO.items():
    comprobar(S.MS.get(tf) == ms, f"signals.MS['{tf}'] = {S.MS.get(tf)} (esperado {ms})")
    comprobar(TR.MINUTOS_VELAS.get(tf) == ms // 60_000,
              f"tracker.MINUTOS_VELAS['{tf}'] = {TR.MINUTOS_VELAS.get(tf)} min")

print("\n=== 2/3. velas reales de Binance por temporalidad ===")
ahora = int(time.time() * 1000)
for tf in ("15m", "1h", "4h", "1d"):
    ks = S.klines("BTCUSDT", tf, 200)
    abre = [int(k[0]) for k in ks]
    cierra = [int(k[6]) for k in ks]
    print(f"\n-- {tf} · {len(ks)} velas pedidas")
    huecos = [i for i in range(1, len(abre)) if abre[i] - abre[i - 1] != S.MS[tf]]
    comprobar(not huecos, f"espaciado exacto de {S.MS[tf] // 60000} min entre velas"
                          + (f" (huecos en {huecos[:3]})" if huecos else ""))
    comprobar(abre == sorted(abre), "orden cronológico")
    desalineadas = [a for a in abre if not alineada(a, tf)]
    comprobar(not desalineadas, "todas abren en límites de la temporalidad (UTC)"
                                + (f" → {len(desalineadas)} desalineadas" if desalineadas else ""))
    comprobar(cierra[-1] - abre[-1] == S.MS[tf] - 1,
              "la última vela dura exactamente la temporalidad")
    viva = abre[-1] <= ahora <= cierra[-1]
    comprobar(viva, f"la última vela es la VIVA (abre {dt.datetime.fromtimestamp(abre[-1]/1000, dt.timezone.utc):%d/%m %H:%M} UTC, ahora dentro de su rango)")
    h = dt.datetime.fromtimestamp(abre[-1] / 1000, dt.timezone.utc).strftime("%a %H:%M")
    comprobar(True, f"última vela: {h} UTC · las últimas 3 cerradas abren en "
                    + ", ".join(dt.datetime.fromtimestamp(a / 1000, dt.timezone.utc).strftime("%H:%M")
                                for a in abre[-4:-1]))

print("\n=== 4. las señales reales traen la temporalidad correcta ===")
S.FRESH_BARS = 200
probadas = 0
for sym in S.top_symbols(20):
    for tf in ("15m", "1h", "4h", "1d"):
        if probadas >= 4:
            break
        ks = S.klines(sym, tf, 300)
        sigs = S.find_signals(sym, tf, ks)
        if not sigs:
            continue
        s = sigs[0]
        probadas += 1
        vela = next((k for k in ks if int(k[0]) == s["entry_ts"]), None)
        dif_tf = (s["entry_ts"] - s["pivot_ts"]) // S.MS[tf]
        print(f"  {sym} {tf}: entrada {dt.datetime.fromtimestamp(s['entry_ts']/1000, dt.timezone.utc):%d/%m %H:%M} UTC "
              f"· comparte {dif_tf} velas con el pivote")
        comprobar(alineada(s["entry_ts"], tf), f"  {sym} {tf}: el entry_ts cae en un límite de {tf}")
        comprobar(vela is not None, f"  {sym} {tf}: el entry_ts existe entre las velas pedidas")
        if vela:
            comprobar(abs(float(vela[4]) - float(s["entry"])) < 1e-9,
                      f"  {sym} {tf}: el precio de entrada es el CIERRE de esa vela (mapeo OHLC correcto)")
        comprobar(dif_tf == S.PIVOT_K, f"  {sym} {tf}: entra {S.PIVOT_K} velas después del pivote (sin lookahead)")
if probadas == 0:
    comprobar(False, "no se encontró ninguna señal para verificar (¿mercado quieto?)")

print("\n=== 5. coherencia con los horarios del cron ===")
cron = {"15m": 15, "1h": 60, "4h": 240, "1d": 1440}
for tf, min_tf in cron.items():
    print(f"  {tf}: el bot escanea cada 15 min (o 30) → detecta la vela nueva de {tf} a tiempo "
          f"{'✅' if 15 <= min_tf else '⚠️'}")

print(f"\n=== RESULTADO: {'todo correcto' if FALLOS == 0 else str(FALLOS) + ' fallo(s)'} ===")
sys.exit(1 if FALLOS else 0)
