#!/usr/bin/env python3
"""
Motor de PUNTAJE de señales (1 a 10), compartido por el estudio y el bot en vivo.

El puntaje NO es una opinión: cada característica entra con el peso medido en `score_study.py`
(que calibra los deciles sobre 3 años de datos y guarda `score_calib.json`). Acá solo viven las
funciones puras: extraer características de una señal y convertir el compuesto en un 1-10.

Características (todas conocidas al momento de la alerta, sin lookahead):
  adx        fuerza de tendencia (ADX 14)
  di         alineación direccional: 1 si −DI > +DI (vendedores dominan)
  vol        volumen de la vela de entrada relativo a su media de 20
  obv        pendiente del OBV de 20 velas normalizada (negativa = distribución; a favor de un short)
  atr        ATR como % del precio (proxy de cuánto pesa la comisión: más alto = mejor)
  rsi        nivel de RSI en el segundo pivote (más alto = divergencia más "de techo")
  rsi_gap    puntos de RSI que se separaron entre los dos pivotes
  px_gap     % de precio entre los dos pivotes (cuánto se estiró el máximo)
  piv_gap    velas entre los dos pivotes
"""
import json
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
CALIB_PATH = os.path.join(HERE, "score_calib.json")

FEATURES = ["adx", "di", "vol", "obv", "atr", "rsi", "rsi_gap", "px_gap", "piv_gap"]

ETIQUETAS = [
    (1, "muy débil"), (2, "débil"), (3, "débil"), (4, "aceptable"), (5, "aceptable"),
    (6, "buena"), (7, "buena"), (8, "fuerte"), (9, "fuerte"), (10, "excepcional"),
]


def features(sig):
    """Características de una señal (dict con adx, plus_di, minus_di, vol_ratio, obv,
    atr_pct, rsi_now, rsi_prev, px_prev, px_now, pivot_gap). Usa None cuando no hay dato."""
    n = sig.get
    f = {}
    f["adx"] = float(n("adx")) if n("adx") is not None else 25.0
    pd, md = n("plus_di"), n("minus_di")
    # para un short, lo que confirma es que dominen los vendedores (−DI > +DI)
    f["di"] = 1.0 if (pd is not None and md is not None and md > pd) else 0.0
    f["vol"] = float(n("vol_ratio") or 0.0)
    f["obv"] = float(n("obv") or 0.0)
    f["atr"] = float(n("atr_pct") or 0.0)
    f["rsi"] = float(n("rsi_now") or 50.0)
    r1, r2 = n("rsi_prev"), n("rsi_now")
    f["rsi_gap"] = abs(float(r1) - float(r2)) if (r1 is not None and r2 is not None) else 0.0
    p1, p2 = n("px_prev"), n("px_now")
    f["px_gap"] = (abs(float(p2) - float(p1)) / float(p1) * 100.0) if (p1 and p2) else 0.0
    f["piv_gap"] = float(n("pivot_gap") or 0.0)
    return f


def load_calib():
    try:
        with open(CALIB_PATH) as fh:
            return json.load(fh)
    except Exception:
        return None


def _bucket_lookup(calib, feat, value):
    """Devuelve el 'poder de predicción' medido de esa característica en el tramo donde cae el valor."""
    return calib["features"].get(feat, {}).get("lookup", {}).get(str(value))


def composite(f, calib):
    """Compuesto = suma de (valor estandarizado × peso medido), aplicando los cortes de la calibración."""
    total = 0.0
    for feat in calib["weights"]:
        w = calib["weights"][feat]
        if abs(w) < 1e-9:
            continue
        spec = calib["features"].get(feat) or {}
        fn = spec.get("fn")
        if fn == "linear":
            mean, sd = spec["mean"], spec["sd"] or 1.0
            z = (f[feat] - mean) / sd
        elif fn == "step":
            z = spec["lookup"].get(_step_key(spec, f[feat]), 0.0)
        elif fn == "binary":
            z = spec["values"].get(str(int(f[feat])), 0.0)
        else:
            z = 0.0
        total += w * z
    return total


def _step_key(spec, value):
    """Clave del tramo: primer corte mayor que el valor, si no el último."""
    for b in spec.get("boundaries", []):
        if value < b:
            return f"<{b}"
    return f">={spec['boundaries'][-1]}" if spec.get("boundaries") else "all"


def score_1_10(f, calib):
    """Puntaje 1-10 según los cortes de deciles medidos, más la etiqueta y el detalle."""
    if not calib:
        return None
    c = composite(f, calib)
    cuts = calib["decile_cuts"]           # 9 cortes -> 10 deciles
    s = 1
    for cut in cuts:
        if c >= cut:
            s += 1
    s = max(1, min(10, s))
    etiqueta = dict(ETIQUETAS).get(s, "")
    return {"score": s, "etiqueta": etiqueta, "compuesto": round(c, 3)}


def explain(f, calib):
    """Por qué ese puntaje: las características con peso, en orden de contribución."""
    if not calib:
        return []
    rows = []
    for feat, w in sorted(calib["weights"].items(), key=lambda kv: -abs(kv[1])):
        spec = calib["features"].get(feat) or {}
        fn = spec.get("fn")
        if fn == "linear":
            z = (f[feat] - spec["mean"]) / (spec["sd"] or 1.0)
        elif fn == "step":
            z = spec["lookup"].get(_step_key(spec, f[feat]), 0.0)
        elif fn == "binary":
            z = spec["values"].get(str(int(f[feat])), 0.0)
        else:
            z = 0.0
        rows.append((feat, w * z, f[feat], spec.get("label", feat)))
    rows.sort(key=lambda r: -abs(r[1]))
    return rows


def bucket_stats(calib, score):
    """Estadística medida del decil del puntaje (para mostrarla en la alerta)."""
    if not calib:
        return None
    for d in calib.get("deciles", []):
        if d["score"] == score:
            return d
    return None
