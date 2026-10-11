#!/usr/bin/env python3
"""Pares propios del usuario: `signals.py` los escanea SIEMPRE, además del top por volumen.

Uso:
  watchlist.py list              → pares propios actuales
  watchlist.py add SOLUSDT XRPUSDT   → agrega (valida que existan en Binance)
  watchlist.py del SOLUSDT       → quita

Se guardan en `watchlist_extra.json`. Para que el cambio se note ya, la watchlist cacheada se
refresca sola; si querés que entre en el próximo escaneo sin esperar, borrá `state.json`.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import signals as S


def cargar():
    try:
        return [s.strip().upper() for s in json.load(open(S.WATCHLIST_EXTRA)) if s.strip()]
    except Exception:
        return []


def guardar(pares):
    with open(S.WATCHLIST_EXTRA, "w") as fh:
        json.dump(sorted(set(pares)), fh, indent=1)


def valido(sym):
    """¿Existe el par en Binance? (una vela diaria alcanza)."""
    try:
        return len(S.klines(sym, "1d", 2)) >= 2
    except Exception:
        return False


def main():
    args = sys.argv[1:]
    if not args or args[0] not in ("list", "add", "del"):
        print(__doc__)
        return 1
    pares = cargar()
    if args[0] == "list":
        print("pares propios:", ", ".join(pares) if pares else "(ninguno)")
        print("se suman al top por volumen en los 4 flujos del bot")
        return 0
    if args[0] == "add":
        for sym in [a.upper() for a in args[1:]]:
            if not valido(sym):
                print(f"  ✗ {sym}: no existe en Binance (o no responde) — no lo agrego")
                continue
            if sym in pares:
                print(f"  = {sym}: ya estaba")
                continue
            pares.append(sym)
            print(f"  ✓ {sym}: agregado")
        guardar(pares)
        print("pares propios ahora:", ", ".join(sorted(set(pares))) or "(ninguno)")
        return 0
    if args[0] == "del":
        for sym in [a.upper() for a in args[1:]]:
            if sym in pares:
                pares.remove(sym)
                print(f"  ✓ {sym}: quitado")
            else:
                print(f"  = {sym}: no estaba")
        guardar(pares)
        return 0


if __name__ == "__main__":
    sys.exit(main())
