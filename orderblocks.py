#!/usr/bin/env python3
"""BLOQUES DE ORDEN (order blocks) — zonas de oferta y demanda.

Definición usada (hay varias en el mercado, así que queda escrita acá para poder medirla y discutirla):
  · Bloque de OFERTA (bajista): la última vela **alcista** antes de una caída fuerte. Se exige que en
    las siguientes MAX_DESPLAZAMIENTO velas el precio caiga ≥ FUERZA_ATR × ATR(14).
  · Bloque de DEMANDA (alcista): la última vela **bajista** antes de una subida fuerte, con la misma
    exigencia al alza.
  · La ZONA del bloque es el **cuerpo** de esa vela (open→close), no toda la mecha.
  · Estado: `fresco` si el precio no volvió a tocarlo después del desplazamiento; `mitigado` si ya
    volvió a entrar; `roto` si lo atravesó (para oferta: cerró por encima de la zona).

Para un SHORT interesa el bloque de oferta **por encima** del precio (donde vender un rebote) y, como
contexto, el de demanda por debajo (dónde puede frenar).

El ATR se recibe calculado (no se recalcula acá) para no duplicar indicadores.
"""

FUERZA_ATR = 1.5          # desplazamiento mínimo, en múltiplos de ATR
MAX_DESPLAZAMIENTO = 3    # velas en las que tiene que ocurrir ese desplazamiento
MAX_VELAS_VIGILANCIA = 400  # hasta cuántas velas atrás se buscan bloques


def _estado(candles, i_fin, z_lo, z_hi, tipo):
    """fresco / mitigado / roto — mira solo lo que pasó DESPUÉS del desplazamiento."""
    tocado = roto = False
    for j in range(i_fin + 1, len(candles)):
        hi, lo, cl = float(candles[j][2]), float(candles[j][3]), float(candles[j][4])
        if lo <= z_hi and hi >= z_lo:
            tocado = True
        if tipo == "oferta" and cl > z_hi:
            roto = True
            break
        if tipo == "demanda" and cl < z_lo:
            roto = True
            break
    if roto:
        return "roto"
    return "mitigado" if tocado else "fresco"


def detectar(candles, atr, fuerza=FUERZA_ATR, max_des=MAX_DESPLAZAMIENTO,
             max_velas=MAX_VELAS_VIGILANCIA):
    """Lista de bloques de orden de las velas cerradas, del más nuevo al más viejo."""
    n = len(candles)
    if n < 30 or not atr:
        return []
    bloques = []
    desde = max(1, n - max_velas)
    for i in range(desde, n - 1):
        a = atr[i]
        if not a or a <= 0:
            continue
        o, c = float(candles[i][1]), float(candles[i][4])
        if o == c:
            continue
        alcista = c > o
        # la vela SIGUIENTE tiene que ir en la dirección del desplazamiento (el bloque es la última
        # vela contraria antes del movimiento, no una vela cualquiera)
        sig_alcista = float(candles[i + 1][4]) > float(candles[i + 1][1])
        if alcista == sig_alcista:
            continue
        fin = min(n - 1, i + max_des)
        if fin <= i:
            continue
        lows = [float(candles[j][3]) for j in range(i + 1, fin + 1)]
        highs = [float(candles[j][2]) for j in range(i + 1, fin + 1)]
        if alcista:                       # vela alcista → posible OFERTA (caída después)
            caida = (float(candles[i][2]) - min(lows)) / a
            if caida < fuerza or min(lows) >= float(candles[i][3]):
                continue                  # exige además romper el mínimo de la vela del bloque
            z_lo, z_hi, tipo = min(o, c), max(o, c), "oferta"
        else:                             # vela bajista → posible DEMANDA (subida después)
            subida = (max(highs) - float(candles[i][3])) / a
            if subida < fuerza or max(highs) <= float(candles[i][2]):
                continue
            z_lo, z_hi, tipo = min(o, c), max(o, c), "demanda"
        bloques.append({
            "tipo": tipo, "idx": i, "ts": int(candles[i][0]),
            "lo": z_lo, "hi": z_hi,
            "fuerza": round(caida if alcista else subida, 2),
            "estado": _estado(candles, fin, z_lo, z_hi, tipo),
        })
    return list(reversed(bloques))


def relevante(bloques, precio, solo_frescos=False):
    """(bloque de oferta más cercano ARRIBA, bloque de demanda más cercano ABAJO).
    Los bloques **rotos** se ignoran: si el precio ya cerró del otro lado, la zona dejó de valer.
    Si el precio está dentro de un bloque de oferta vigente, ese es el que devuelve como 'arriba'."""
    vigentes = [b for b in bloques if b["estado"] != "roto"]
    dentro = next((b for b in vigentes if b["tipo"] == "oferta" and b["lo"] <= precio <= b["hi"]), None)
    ofertas = [b for b in vigentes if b["tipo"] == "oferta" and b["lo"] > precio
               and (not solo_frescos or b["estado"] == "fresco")]
    demandas = [b for b in vigentes if b["tipo"] == "demanda" and b["hi"] < precio
                and (not solo_frescos or b["estado"] == "fresco")]
    arriba = dentro or (min(ofertas, key=lambda b: b["lo"] - precio) if ofertas else None)
    abajo = max(demandas, key=lambda b: b["hi"]) if demandas else None
    return arriba, abajo


# Histórico MEDIDO de cada caso (`bloques_estudio.py`: 333 señales de short con volumen, 15 pares,
# 4h y 1h, ~3 años, salida 2×ATR/1×ATR, comisión 0.10%/lado). Referencia del conjunto: −0.16% por
# señal. Ningún caso es positivo en las 3 ventanas: el bloque se muestra como CONTEXTO, no como ventaja.
MEDIDO = {
    "en_oferta": (-0.49, 53, "-0.30/-1.20/-0.57"),
    "oferta_cerca": (-0.33, 125, "-0.18/-0.73/+0.00"),
    "demanda_cerca": (-0.14, 103, "+0.22/+0.10/-0.66"),
    "sin_oferta": (-0.05, 208, "-0.28/+0.09/-0.31"),
}


def linea(sig, precio=None):
    """Texto para la alerta: bloque de oferta arriba y de demanda abajo (o '' si no hay), con el
    histórico medido de ese caso — así el bloque se lee como contexto y no como una ventaja."""
    bloques = sig.get("_bloques")
    if not bloques:
        return ""
    p = float(precio or sig.get("entry") or 0)
    arriba, abajo = relevante(bloques, p)
    partes = []
    if arriba:
        dentro = arriba["lo"] <= p <= arriba["hi"]
        etiqueta = ("🧱 El precio está DENTRO de la banda de VENTA (oferta)" if dentro
                    else "🧱 Banda de VENTA (oferta) arriba")
        dist = "" if dentro else f" (a +{(arriba['lo'] / p - 1) * 100:.2f}%)"
        partes.append(f"{etiqueta} {arriba['lo']:.6g}–{arriba['hi']:.6g}{dist} · {arriba['estado']} "
                      f"· desplazamiento {arriba['fuerza']}×ATR")
        # el número medido solo se pega si corresponde EXACTAMENTE al caso: el histórico de
        # "oferta fresca ≤3% arriba" no aplica a un bloque ya mitigado
        if dentro:
            m, etiqueta_m = MEDIDO["en_oferta"], "estando dentro de un bloque de oferta"
        elif arriba["estado"] == "fresco" and (arriba["lo"] / p - 1) <= 0.03:
            m, etiqueta_m = MEDIDO["oferta_cerca"], "con una oferta fresca a ≤3% arriba"
        else:
            m, etiqueta_m = None, None
        if m:
            partes.append(f"📊 Medido {etiqueta_m}: {m[0]:+.2f}% por señal (n={m[1]}, ventanas {m[2]})"
                          + (" ⚠️ peor que el promedio (−0.16%)" if m[0] < -0.16 else ""))
        elif arriba["estado"] == "mitigado":
            partes.append("ℹ️ Bloque ya mitigado (el precio lo tocó): sin medición propia, "
                          "así que no lleva número")
    if abajo:
        partes.append(f"🧱 Banda de COMPRA (demanda) abajo {abajo['lo']:.6g}–{abajo['hi']:.6g} "
                      f"(−{(1 - abajo['hi'] / p) * 100:.2f}%) · {abajo['estado']}")
        if abajo["estado"] == "fresco" and (1 - abajo["hi"] / p) <= 0.03:
            m = MEDIDO["demanda_cerca"]
            partes.append(f"📊 Medido con demanda fresca ≤3% abajo: {m[0]:+.2f}% por señal (n={m[1]})")
    return "\n".join(partes)
