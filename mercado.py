#!/usr/bin/env python3
"""AVISOS DE CONTEXTO de mercado — no son señales de entrada, son el fondo de pantalla.

Qué mira (sobre los 20 pares más líquidos + BTC y ETH):
  · movimiento de BTC en 15m y en 1h
  · funding del perpetuo en extremos (BTC/ETH): quién le está pagando a quién
  · amplitud: cuántos de los 20 pares se movieron ≥4% en la última hora

Umbrales fijos y explícitos (no son una estrategia: son avisos de "ojo, el mercado se movió"):
  BTC 15m ≥ 2.5% · BTC 1h ≥ 4% · |funding| ≥ 0.05%/8h · amplitud ≥ 8 pares con ≥4% en 1h
Anti-repetición: cada tipo de aviso, como máximo una vez cada 2 h (`.mercado_estado.json`).
Cero tokens: el script arma y manda el mensaje él mismo.
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import signals as S

ESTADO = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".mercado_estado.json")
CANAL = "1551743637434671167"        # #contexto-mercado  (--canal lo cambia)
UMB_BTC_15M, UMB_BTC_1H, UMB_FUNDING, UMB_AMPLITUD = 2.5, 4.0, 0.05, 8
ESPERA = 2 * 3600


def mov(sym, tf, velas=1):
    """% de movimiento en las últimas `velas` de esa temporalidad (velas cerradas)."""
    ks = [c for c in S.klines(sym, tf, 6)][:-1]
    if len(ks) <= velas:
        return None, None
    ini, fin = float(ks[-1 - velas][1]), float(ks[-1][4])
    return (fin / ini - 1) * 100, fin


def evaluar():
    eventos = []
    btc15, precio = mov("BTCUSDT", "15m")
    btc1h, _ = mov("BTCUSDT", "1h")
    if btc15 is not None and abs(btc15) >= UMB_BTC_15M:
        eventos.append(("btc15", f"• BTC {btc15:+.2f}% en 15m (precio {precio:,.0f} USDT)"))
    if btc1h is not None and abs(btc1h) >= UMB_BTC_1H:
        eventos.append(("btc1h", f"• BTC {btc1h:+.2f}% en 1h"))
    for sym in ("BTCUSDT", "ETHUSDT"):
        d = S.derivados(sym)
        f = d.get("funding")
        if f is not None and abs(f) >= UMB_FUNDING:
            quien = "longs pagan a shorts" if f > 0 else "shorts pagan a longs"
            eventos.append((f"fund_{sym}", f"• Funding {sym}: {f:+.4f}%/8h — extremo ({quien})"))
    pares = S.top_symbols(20)
    movidos = []
    for sym in pares:
        try:
            m, _ = mov(sym, "1h")
        except Exception:
            continue
        if m is not None and abs(m) >= 4:
            movidos.append((sym, m))
        time.sleep(0.04)
    if len(movidos) >= UMB_AMPLITUD:
        top = sorted(movidos, key=lambda t: -abs(t[1]))[:5]
        detalle = ", ".join(f"{s.replace('USDT','')} {m:+.1f}%" for s, m in top)
        eventos.append(("amplitud", f"• Amplitud: {len(movidos)} de {len(pares)} pares con ≥4% en 1h "
                                    f"→ mercado movido ({detalle})"))
    return eventos


def main():
    ap = argparse.ArgumentParser(description="Avisos de contexto de mercado")
    ap.add_argument("--dry-run", action="store_true", help="imprime, no envía")
    ap.add_argument("--estado", action="store_true", help="muestra la foto actual sin enviar nada")
    ap.add_argument("--canal", default="", help="ID de canal de Discord")
    args = ap.parse_args()

    def log(msg):
        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}")

    if args.canal:
        S.DISCORD_OVERRIDE = args.canal
    elif not args.estado:
        S.DISCORD_OVERRIDE = CANAL
    S.SEND_TELEGRAM = False

    eventos = evaluar()
    if args.estado:
        print("eventos detectados ahora:", eventos or "ninguno")
        return 0
    # anti-repetición: no repetir el mismo tipo de aviso dentro de 2 h
    try:
        previos = json.load(open(ESTADO))
    except Exception:
        previos = {}
    ahora = time.time()
    nuevos = [(k, t) for k, t in eventos if ahora - previos.get(k, 0) > ESPERA]
    if not nuevos:
        log(f"sin novedades ({len(eventos)} evento(s) dentro del silencio de 2 h)")
        return 0
    for k, _ in nuevos:
        previos[k] = ahora
    txt = ("🌡️ **CONTEXTO DE MERCADO**\n" + "\n".join(t for _, t in nuevos) +
           "\nℹ️ Es contexto para leer tus señales, no una señal de entrada: el bot no opera.")
    if args.dry_run:
        print(txt)
        return 0
    log(f"aviso enviado por Discord: {S.deliver(txt)}")
    json.dump(previos, open(ESTADO, "w"), indent=1)
    log(f"total de avisos nuevos: {len(nuevos)}")


if __name__ == "__main__":
    main()
