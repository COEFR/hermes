# trading — bot de señales de cripto (divergencias de RSI)

Creado 2026-09-20. Solo **informa**: no opera, no pide API keys, no toca fondos.

## Archivos (el directorio del proyecto)
| Archivo | Qué es |
|---|---|
| `backtest.py` | Backtest de la estrategia sobre datos públicos de Binance. Sin pandas: indicadores Wilder y ATR en Python puro. Cachea velas en `cache/`. |
| `backtest_results.json` | Resultados por temporalidad × variante × comisión (0.10 / 0.05 / 0.02 % por lado). |
| `signals.py` | Bot en vivo. Escanea y **manda las alertas por la Bot API de Telegram** (cero tokens de LLM). |
| `chart.py` | Dibuja el PNG de cada señal (velas + RSI + pivotes + niveles) para adjuntar a la alerta. Necesita el venv del bot. |
| `venv/` | Entorno del bot con Pillow (`uv venv` + `uv pip install pillow`); el cron usa ESTE python. |
| `state.json` | Watchlist cacheada, señales ya enviadas (dedupe) y cooldown por par. |
| `logs/` | `signals.log` (rotado a 2 MB), `cron.log`. |

## Cómo se ejecuta
- Cron del sistema (`crontab -l`), servicio `cron` **enabled** (sobrevive reinicios; no depende del gateway de Hermes):
  - `*/15 * * * *` → `signals.py` (solo manda si hay señal nueva; silencio si no hay).
  - `0 12 * * *` → `signals.py --report` (resumen diario, 08:00 Bolivia).
- Manual: `python3 signals.py --dry-run` (no envía) · `--report` · `--trend` (activa filtro EMA200 del TF superior, desactivado por defecto).

## Estrategia
- Watchlist dinámica: top 20 pares USDT por volumen 24 h; excluye stablecoins y tokens apalancados (`UP/DOWN/BULL/BEAR`). Se refresca cada 6 h.
- RSI(14) Wilder + divergencia regular entre los dos últimos pivotes de swing (pivote = extremo de ±3 velas; comparación a ≤60 velas).
- Mínimos de calidad: 0.5 % de diferencia de precio y 2 puntos de RSI entre pivotes.
- Filtro de volumen (la vela de entrada ≥ 1.2× SMA20 del volumen) — **el único filtro que mejoró el resultado en el backtest**.
- Entrada al cierre de la vela que confirma el pivote (pivote + 3), solo si es de las últimas 2 velas cerradas → sin lookahead.
- TP 1.5×ATR14, SL 1.0×ATR14. Cooldown 4 h por par (anti-spam: caso LAUSDT con 8 señales repetidas).
- Filtro de tendencia EMA200 del TF superior: implementado pero **apagado**, porque en el backtest empeoró (1h 36.2 %, 4h 36.4 %).

## Hallazgos del backtest (15 pares líquidos, 1 año, 2025-09 → 2026-09)
Win rate de **equilibrio** (TP 1.5R/SL 1R + comisión de 0.10 % por lado), calculado como
`(1+drag)/((1.5−drag)+(1+drag))` con el drag medido por temporalidad:

| TF | stop medio (ATR) | equilibrio spot 0.10 % | win rate medido base | con filtro de volumen |
|---|---|---|---|---|
| 15m | 0.89 % | 49.0 % | 40.3 % (n=1661) | 45.0 % (n=353) |
| 1h | 1.57 % | 45.1 % | 34.8 % (n=857) | 31.0 % (n=171) |
| 4h | 2.80 % | 42.9 % | 37.6 % (n=295) | **55.0 % (n=60)** |
| 1d | 8.16 % | 41.0 % | 41.5 % (n=41) | 33.3 % (n=3) |

- La comisión pesa más que la temporalidad: en 15m el drag es 0.224 R por operación (22 % del riesgo); en 1d, 0.024 R.
- Tasa base (entradas ciegas, misma estructura): 15m 40.8 %, 1h 38.1 %, 4h 41.6 %, 1d 44.8 % → la estructura TP1.5/SL1 sola **no** tiene ventaja.
- Único resultado con ventaja real: **4h + filtro de volumen** → 55.0 % (IC95 42.5–66.9), esperanza +0.31 R, PF 1.64, +18.5 R en un año, ~0.33 señales/par/mes.
- Cooldown: no cambia el win rate (40.6 % vs 40.3 % en 15m); sirve para el spam, no para la rentabilidad.

## Paso 2 — rentabilidad en dinero (`profit.py`, `profit_results.json`)
Riesgo 1% del capital por operación, compuesto, orden cronológico, 365 días, 15 pares. `1.00` = cuenta sin cambios.

**Cartera como está el bot (las 4 temporalidades, con filtro de volumen):**
| salida | comisión 0.10% spot | 0.05% futuros | 0.02% maker |
|---|---|---|---|
| TP1.5/SL1.0 | 0.347 (**−65.3%**, DD −70.0%) | 0.683 (−31.7%) | 1.024 (+2.4%) |

**Solo 4h + filtro de volumen** (60 operaciones/año, ~5/mes):
| salida | 0.10% | 0.05% | 0.02% | DD (a 0.10%) | aciertos |
|---|---|---|---|---|---|
| TP1.5/SL1.0 | +19.7% | +22.8% | +24.7% | −5.5% | 55.0% |
| TP2.0/SL1.0 | +25.7% | +29.0% | +31.0% | −5.5% | 48.3% |
| TP3.0/SL1.0 | +33.5% | +36.9% | +39.1% | −10.0% | 40.0% |

- **La cartera completa pierde por culpa de 15m y 1h** (353 y 171 señales que pierden) tapando a la 4h (60 que gana). Limitarse a 4h es lo que hace la diferencia.
- **Consistencia** (TP2.0): 1ª mitad del año +16.1% (n=27, 51.9% aciertos), 2ª mitad +12.9% (n=33, 45.5%). TP3.0: +16.3% / +19.6%. **No depende de un tramo.**
- **Por par** (TP2.0): XRP +0.98R (n=6), G +1.02R (n=6), SOL +0.29R (n=7), ONE +0.52R, DOGE +0.51R → no lo sostiene un solo par.
- **La salida pesa tanto como la señal**: misma señal de 4h, de TP1.5 a TP3 el retorno sube de +19.7% a +33.5% aunque el acierto baja de 55% a 40%.
- ❗ 216 celdas probadas (6 salidas × 4 TF × 3 variantes × 3 comisiones) y n=60 en la mejor: parte del brillo puede ser azar. Consistencia entre mitades y pares lo hace menos probable, no imposible.
- ❗ **No incluye slippage** (asume entrada exacta al cierre) ni comisiones reales del exchange elegido.

## Paso 3a — ¿qué agregar para que sea rentable? (`addons.py`, `addons_results.json`)
Referencia = señal + filtro de volumen + salida TP2×ATR/SL1×ATR. Métrica: **% de movimiento del precio por alerta**, comisión 0.10%/lado.

**Sí suma**
| qué se agrega | 15m | 1h | 4h |
|---|---|---|---|
| referencia | −0.05% | −0.68% | +1.27% |
| **+ volatilidad ATR ≥ 1.5%** | **+1.09%** | −1.01% | **+1.45%** |
| + ATR ≥ 2% | +2.11% (n=16) | −0.70% | +1.61% |
| + RSI en extremo (<35 / >65) | −0.04% | −0.37% | +1.45% |
| + BTC a favor (EMA200 4h) | +0.10% | −1.12% | +0.55% |
| + vela de confirmación | +0.02% | −0.70% | +1.11% |
| + divergencia ancha (≥20 velas) | −0.21% | s/m | s/m |
| salida trailing 2×ATR | −0.20% | −1.02% | **−0.75%** |
| salida break-even tras +1R | −0.39% | −0.97% | **−0.25%** |

**Recetas (con corte en dos mitades del año)**
| receta | alertas/mes | aciertos | mueve/alerta | 1ª mitad | 2ª mitad |
|---|---|---|---|---|---|
| **15m+4h + vol + ATR≥1.5%** | 6.8 | 47.6% | **+1.33%** | **+1.23%** | **+1.43%** |
| 4h + vol + ATR≥1.5% | 4.6 | 50.9% | +1.45% | +1.13% | +1.75% |
| solo 4h + vol + ATR≥2% | 3.5 | 50.0% | +1.61% | +1.06% | +2.16% |
| 15m+4h + vol + ATR≥1.5% + RSI extremo | 2.1 | 60.0% | +2.21% | +0.24% | +3.52% |
| 1h + vol + ATR≥1.5% | 6.3 | 18.4% | **−1.01%** | −1.13% | −0.88% |

**Conclusiones**
- El añadido que más aporta es **volatilidad mínima (ATR ≥ 1.5% del precio)**: con el stop tan ajustado de 15m (0.9%) la comisión se comía 0.22 R por operación; con stops ≥1.5% pesa mucho menos. Cambia el 15m de −0.05% a +1.09% por alerta.
- **El 1h no lo salva ningún filtro**: hay que sacarlo del bot.
- **No tocar la salida**: trailing y break-even empeoran (break-even deja el 4h en 20% de aciertos).
- RSI en extremo sube el promedio pero **no aguanta las dos mitades** (n chico): descartado por ahora.
- ❗ 82 señales en el año para la receta principal: falta walk-forward de 3 años antes de confiar.

## Bot de alertas v2 (2026-09-20) — especificación pedida
`signals.py`, por cron cada 15 min. **Solo alertas**: no opera, no pide claves, no toca fondos.

| Qué | Valor por defecto | Flag |
|---|---|---|
| Lado | **shorts** (divergencia bajista) | `--side short\|long\|both` |
| Temporalidades | **1h, 4h, 1d** | `--intervals` |
| Volumen | vela de entrada ≥ 1.2× la media de 20 | `--no-vol` lo apaga |
| ADX(14) | **≥ 25** | `--min-adx 0` lo apaga |
| Salida mostrada | objetivo 2×ATR, stop 1×ATR | constantes `TP_MULT`/`SL_MULT` |
| Cooldown | 4 h por par + dedupe por pivote | `COOLDOWN_H` |
| Frescura | señal válida si la vela que confirma es de las últimas 2 cerradas | `--fresh-bars` |

La alerta incluye: divergencia con los dos pivotes de precio y RSI, ADX con +DI/−DI e
interpretación, volumen relativo, OBV de 20 velas, entrada/stop/objetivo, % de riesgo, ATR% del
precio y el histórico medido de esa temporalidad. Flags de prueba: `--dry-run`, `--no-state`.

Cron: `signals.py --side short --min-adx 25 --intervals 1h,4h,1d` (*/15) y `--report` (12:00 UTC).

## Paso 3b — shorts con ADX y volumen (`shorts_adx.py`, `shorts_results.json`)
1.316 señales de divergencia bajista en el año (15 pares). Salida 2×ATR/1×ATR, comisión 0.10%/lado.

| filtros | 15m | 1h | 4h |
|---|---|---|---|
| solo divergencia bajista | −0.11% (66/mes) | −0.42% (32/mes) | −0.53% (10.6/mes) |
| + volumen ≥1.2× | +0.13% (12.9/mes) | −0.67% (6.8/mes) | **+2.00%** (1.7/mes) |
| + ADX ≥ 25 | −0.19% (50/mes) | −0.25% (22.7/mes) | −0.02% (6.6/mes) |
| + volumen y ADX ≥ 25 | −0.02% (8.5/mes) | +0.27% (2.9/mes) | **+4.30%** (1.0/mes) |
| + vol + ADX≥25 + −DI>+DI | −0.76% (1.2/mes) | s/m | s/m |
| + vol + ADX≥25 + ATR ≥1.5% | +1.84% (1.0/mes) | +0.76% (1.2/mes) | +4.79% (0.9/mes) |

- **El ADX solo empeora** (−0.18% en 15m vs −0.11% sin filtros). Combinado con volumen es la mejor
  combinación (4h: 58.3% al objetivo, +4.30% por alerta, mitades +5.02% / +3.57%).
- **Exigir −DI>+DI (que dominen los vendedores) empeora** (−0.76% en 15m, n=14): se muestra en la
  alerta como contexto, NO se filtra por eso.
- En shorts el 15m no aporta: fuera de las temporalidades por defecto.
- Validación del ADX propio: serie sintética de subida sostenida → ADX 100 (+DI 75/−DI 0);
  lateral → 21.9; coincide con la copia de `backtest.py`. En BTC 4h real: mediana 21.5, máx 40.6.
- ⚠️ Muestras chicas: la mejor celda (4h) son 12 señales en el año.

## 15m agregado (2026-09-20, pedido)
Cron y defaults ahora escanean **15m, 1h, 4h, 1d**. Medición del efecto (shorts + volumen + ADX≥25, cooldown 4 h):

| configuración | alertas/mes | n | al objetivo | mueve/alerta | 1ª mitad | 2ª mitad |
|---|---|---|---|---|---|---|
| **15m+1h+4h+1d** (lo que corre) | 12.4 | 149 | 36.2% | **+0.50%** | +0.48% | +0.52% |
| 15m+1h+4h+1d +ATR≥1.5% | 3.2 | 38 | 50.0% | **+2.71%** | +3.41% | +2.02% |
| 15m sola | 8.4 | 101 | 32.7% | −0.04% | −0.16% | +0.08% |
| 15m sola +ATR≥1.5% | 1.0 | 12 | 50.0% | +1.84% | +1.90% | +1.78% |
| 1h+4h+1d (antes) | 4.1 | 49 | 44.9% | +1.77% | +1.82% | +1.71% |

- Agregar 15m **triplica los avisos** (4.1 → 12.4/mes) pero **baja el valor medio por alerta de +1.77% a +0.50%**:
  las alertas de 15m valen −0.04% cada una y son 8.4 de las 12.4.
- El añadido que lo compensa es `--min-atr-pct 1.5` (filtro de volatilidad): 3.2 alertas/mes a **+2.71%** por alerta,
  consistente en las dos mitades. En 15m el stop medio es 0.89% del precio, así que la comisión se come 0.22 R por
  operación; con ATR ≥1.5% pesa mucho menos.
- Cada alerta de 15m muestra su propio histórico en el mensaje (33.3% al objetivo, −0.02% de media), para que se
  vea de dónde viene la señal.

## Dos niveles de alerta (2026-09-20, pedido)
`⭐ PREMIUM` = divergencia + volumen ≥1.2× + ADX ≥ 25 · `📡 NORMAL` = divergencia + volumen con ADX < 25.
Cada alerta dice su nivel y muestra el histórico medido de ESE nivel (`shorts_results.json → tiers`).

| TF | nivel | alertas/mes | n | al objetivo | mueve/alerta | 1ª mitad | 2ª mitad |
|---|---|---|---|---|---|---|---|
| 15m | PREMIUM | 8.4 | 101 | 32.7% | −0.04% | −0.16% | +0.08% |
| 15m | NORMAL | 4.4 | 53 | **54.7%** | **+0.42%** | +0.32% | +0.53% |
| 1h | PREMIUM | 2.9 | 35 | 40.0% | +0.27% | −0.17% | +0.67% |
| 1h | NORMAL | 3.9 | 47 | 8.5% | **−1.37%** | −0.92% | −1.68% |
| 4h | PREMIUM | 1.0 | 12 | 58.3% | **+4.30%** | +5.02% | +3.57% |
| 4h | NORMAL | 0.7 | 8 | 25.0% | −1.44% | −2.12% | −0.76% |

- **Hallazgo contraintuitivo**: en 15m el nivel NORMAL (sin tendencia, ADX<25) es mejor que el premium
  (54.7% al objetivo y +0.42% por alerta, consistente en las dos mitades) — la divergencia es una señal
  contra-tendencia, así que pelea contra el ADX alto.
- En 1h y 4h el nivel NORMAL es claramente malo (−1.37% y −1.44%): ahí manda el PREMIUM.
- Configuración recomendada y usada en el cron: `--normal-intervals 15m` (el normal solo en 15m).
  Total ≈ 17 alertas/mes (12.4 premium + 4.4 normal). Con el normal en todas las TF serían ≈21/mes,
  pero 4.6 de esas son las de 1h/4h con valor negativo.
- Flags nuevos: `--normal-sin-volumen` (el normal tampoco exige volumen: ~+80/mes, valor negativo),
  `--normal-intervals` (dónde se permite el normal).

## Puntaje de señales 1-10 (2026-09-20, pedido)
`scoring.py` (motor compartido) + `score_study.py` (calibrador) + `score_calib.json`. **Sin pesos
inventados**: cada característica entra por el tramo (tertiles) con el % de movimiento que ese tramo
rindió en el histórico, y pesa según su dispersión medida (≥0.08 puntos %).

| característica | tramo bajo | medio | alto | dispersión | peso |
|---|---|---|---|---|---|
| separación del RSI entre pivotes | −0.17% | −0.38% | **+0.91%** | 1.29 | 23% |
| ADX | −0.36% | +0.02% | **+0.69%** | 1.05 | 19% |
| ATR % del precio | −0.03% | −0.30% | **+0.69%** | 0.99 | 18% |
| volumen relativo | **+0.65%** | −0.10% | −0.20% | 0.85 | 15% |
| velas entre pivotes | +0.48% | −0.12% | +0.01% | 0.60 | 11% |
| OBV | −0.08% | −0.04% | +0.47% | 0.55 | 10% |
| salto de precio | +0.03% | +0.19% | +0.13% | 0.16 | 3% |
| −DI vs +DI | 0.00% | 0.00% | +0.12% | 0.12 | 2% |

Deciles medidos (259 señales de short con volumen, 1 año): el **10/10 rinde +3.72% por alerta con 57.7%
al objetivo** (n=26); el extremo opuesto (1/10) +0.17%; **el medio (4–8) es donde se pierde** (−0.1% a
−1.1%). Control en mitades: puntaje ≥7 → +0.74% / +0.88% (positivo en las dos); ≤4 → −0.13% / −0.02%.

- El bot puntúa cada señal en vivo (`signals.py` importa `scoring.py`), muestra **por qué** (las 3
  características de mayor contribución, a favor o en contra), el histórico del decil y el control en mitades.
- `--min-score` (0 = manda todas, para no perder volumen; 7+ = solo las fuertes).
- ⚠️ La calibración es **en muestra**: es optimista por construcción. Se re-calibra con la ventana de
  3 años cuando esté la caché, y la validación fuera de muestra (`oos.py`) manda.

## ⛔ VEREDICTO DE LA VALIDACIÓN (2026-09-20) — la estrategia NO tiene ventaja medible
`oos.py` (3 años, 3 ventanas de 1 año) y `survive.py` (todas las celdas cruzadas):

| ventana | bot completo | solo premium | premium 4h |
|---|---|---|---|
| 2023-09 → 2024-09 | −0.24% (n=232) | −0.30% (n=156) | −0.59% (n=14) |
| 2024-09 → 2025-09 | −0.13% (n=311) | −0.18% (n=214) | −0.06% (n=24) |
| **2025-09 → 2026-09** (la usada para elegir los filtros) | **+0.49%** (n=201) | **+0.50%** (n=149) | **+4.30%** (n=12) |

- **`survive.py`: 0 de 10 celdas** (dirección × temporalidad × nivel, n≥45) son positivas en las tres
  ventanas. La más estable, SHORT 15m premium, da +0.11% / +0.08% / −0.04% → ruido sin ventaja.
- Diagnóstico: **sobreajuste**. Todo lo bueno que medimos (el +4.30% del 4h premium, los deciles del
  puntaje) venía de la única ventana que se usó para elegir los filtros.
- Histórico honesto (3 años) de los niveles que muestra el bot hoy: 15m premium +0.05%, 15m normal
  +0.06%, 1h premium −0.21%, 1h normal −0.44%, 4h premium +0.84% (mitades −0.51% / +2.18%), 4h normal −0.49%.
- Puntaje recalibrado con 3 años: el decil 10 pasa de +3.72% (1 año) a **+0.13%**; el orden se mantiene
  débil (≥7 → +0.11% / +0.28%; ≤4 → −0.45% / −0.13%), pero es *menos malo vs malo*, no bueno vs malo.
- **Consecuencia**: el bot sirve como radar informativo, no como fuente de rentabilidad. No poner dinero.
- Regla fijada de antemano y cumplida: si no aguanta fuera de muestra, se abandona la expectativa de plata.

## Paso 4 — salidas y volumen (2026-09-20)
- **11 estilos de salida medidos** sobre las 916 señales de short, en las 3 ventanas (`salidas.py`): **0 sobrevive**.
  Las "salidas inteligentes" son **las peores**: break-even tras +1R −0.45/−0.77%, parciales 1.5R+3R ≈ −0.4%,
  salir cuando el RSI cruza 50 ≈ −0.35%, salir por tiempo ≈ −0.35%. La mejor: **objetivo en el soporte real con
  stop 1.5×ATR** (−0.05% / −0.03% / **+0.48%**: la única que nunca queda claramente negativa) → es la que muestra el bot.
- El bot ahora publica el **R:R explícito** y avisa cuando el soporte está más cerca que el stop.
- ⚠️ La línea "Histórico X/10" sigue medida con la salida fija 2×ATR/1×ATR (así está etiquetada).
- **Volumen ampliado** (pedido): cron con `--min-adx 20` y nivel normal en todas las TF → de ~20 a ~26 alertas/mes.
- Candidatas de entrada medidas y descartadas (`candidatas.py`): oculta, confluencia S/R, RSI>70, RSI(9),
  activo bajista (EMA200 1D) y BTC bajista → **0 de 7** positiva en las 3 ventanas.

## Shortear una subida parabólica (2026-09-20, consulta puntual por AVAX)
Script nuevo: un script de análisis puntual (fuera del repo) (usa la caché de 3 años del bot). Estado medido al cierre
de la vela: **RSI(14) ≥ 75 · ADX(14) ≥ 40 · +DI > −DI** (subida fuerte y sobrecompra). Short al cierre,
stop 1.5×ATR, objetivo 2×ATR, comisión 0.05%/lado, cooldown 6 velas/par, empate = SL. 15 pares, 3 años.

| caso | n | al objetivo | mueve/señal | ventanas (1ª/2ª/3ª) |
|---|---|---|---|---|
| 4h RSI≥75 ADX≥40 | 409 | 35.5% | **−0.84%** | −0.97% / −0.65% / −0.89% |
| 4h RSI≥70 ADX≥40 | 635 | 37.2% | −0.66% | −0.76% / −0.68% / −0.52% |
| 4h RSI≥70 ADX≥25 | 1305 | 36.9% | −0.60% | −0.86% / −0.38% / −0.58% |
| 1h RSI≥75 ADX≥40 | 1620 | 37.2% | −0.41% | −0.30% / −0.47% / −0.45% |
| **solo AVAX, 4h parabolico** | 21 | 38% | −0.24% | — |

- El equilibrio con esa estructura es ~43% al objetivo: **ningún caso llega**; y **negativo en las tres ventanas**
  en las cuatro variantes → no es azar de una ventana.
- Forward: tras el estado parabólico el precio **sigue subiendo** en promedio (+2.13% a 24 h en 4h; +3.76% a 48 h)
  y solo 46–47% de las veces cerró abajo. Comprar la caída de una parábola es la peor forma de operarla.
- Conclusión operativa para responder consultas tipo «¿le tiro un short a X que está subiendo?»: sobrecompra
  **no** es señal de short; el bot no dispara nada (la divergencia exige dos máximos con RSI menor) y este
  atajo medido pierde plata. Respuesta = veredicto llano + estos números + qué se necesitaría (divergencia real).

## Gráficos en las alertas (2026-09-21, pedido)
`chart.py` dibuja un PNG por señal (1280×830, fondo #050505, ámbar #FFB000, rojo #E5484D para el
riesgo, verde #26C281 para el objetivo): velas de las últimas 90 + volumen + RSI 14 en panel aparte,
los dos pivotes marcados con su RSI, la línea de divergencia, y las líneas de entrada / stop /
objetivo / soporte con su % — más un pie con ADX, +DI/−DI, ATR%, volumen, riesgo y R:R.

- **Pillow va en el venv propio del bot** (`el directorio del proyecto/venv`, creado con `uv`) y el **cron
  pasó a ese python** (5 líneas; respaldo en `cache/scratch/crontab.bak`). El python del sistema no
  tiene Pillow.
- El gráfico **nunca bloquea la alerta**: `grafico(s)` está en try/except y devuelve None. Flag `--no-chart`.
- Envío: Telegram `sendPhoto` multipart (caption ≤1024 → el texto largo va en un mensaje aparte) y
  Discord `files[0]` + `payload_json` en la primera parte. Multipart a mano con stdlib.
- Los PNG viven en `charts/` y se conservan los últimos 400.
- ✅ **Verificado end-to-end**: mensaje en #hermes con adjunto de 49 KB, 1280×830, y el PNG bajado de
  Discord resultó **byte a byte idéntico** al archivo local.
- ⚠️ Tres trampas corregidas en el camino (todas hacían que el gráfico **mintiera**, no que fallara):
  (1) el RSI calculado sobre la ventana recortada daba valores falsos por falta de calentamiento
  (61.5 vs 59.2 real) → se calcula sobre la serie completa y se recorta después; (2) en momentum y
  rebote `rsi_prev`/`rsi_now` son **relleno 50/50** → el gráfico filtra por tipo (`"Clásica"`) para no
  inventar una divergencia que no existe; (3) la ventana de 90 velas se comía los pivotes en señales
  no frescas (gráfico sin divergencia) y, al hacerla adaptativa, el recorte del RSI usaba
  `len(serie) - len(ventana)` en vez del **índice inicial** de la ventana → el panel mostraba el RSI de
  otras velas (65.9/54.1 en lugar de 61.9/59.1). Ahora la ventana se corre para incluir siempre la
  divergencia y el RSI se recorta con su índice real.
- 🧪 **Control repetible**: `chart_check.py` (4 señales reales) compara el RSI dibujado en los pivotes
  contra el de la señal y sale con código 1 si no coinciden. Última corrida: **4 probadas, 0 fallas**.
  Correrlo después de tocar `chart.py`.
- 🔴 **Error mío, corregido**: la corrida de prueba del flujo de momentum **sí envió** — en la rama
  `--individual` el `deliver()` estaba antes del `if dry`. Mandó 13 alertas a Telegram y al canal de
  señales. Arreglado (el dry-run ahora retorna antes de enviar) y verificado contando `enviado ok=` en
  `logs/signals.log`: 103 antes, 103 después. Los 13 del canal de Discord se borraron por API (con
  pausas: los DELETE en ráfaga dan 429); **los de Telegram no se pueden borrar por API** (no permite
  listar los mensajes propios). Regla: probar flujos nuevos con `--no-telegram` + un canal de pruebas.

## Seguimiento y vigilancia (2026-09-21) — el server ahora sabe si sus señales salen bien
Hueco que tenía el server: mandaba alertas y **nunca decía cómo terminaban**; y si el bot se caía, el
silencio se leía como "no hay señales".

- `signals.py::registrar()` — al enviar cada alerta anota la señal en **`signals_log.jsonl`** (par, TF,
  dirección, entrada/stop/objetivo, puntaje, nivel, flujo, timestamps). Sin esto no hay seguimiento posible.
- `tracker.py` — cada 15 min (cron `5,20,35,50`) lee ese log, evalúa cada señal contra las velas
  posteriores de Binance y publica el cierre en **#resultados-señales** (`1551671378065752255`):
  ✅ objetivo / ❌ stop / ⏳ por tiempo, con % de movimiento, duración en velas y **marcador acumulado**.
  Reglas **idénticas al backtest** (empate objetivo/stop en la misma vela → stop) para que las cifras
  sean comparables; máximo 50 velas antes de cerrar por tiempo.
  - `--resumen [--dias N]` arma el marcador: n, % al objetivo, % por alerta, desglose por TF/nivel y
    comparación con el backtest de ese mismo nivel. Cron semanal: lunes 13:00 UTC (`0 13 * * 1`), a
    Discord + Telegram.
  - `--ejemplos N` evalúa señales reales recientes sin tocar los archivos (así se probó el formato).
  - ⚠️ Los flujos de **momentum y rebote no tienen stop/objetivo medido** → quedan como `sin_niveles` y no
    se pueden seguir hasta que se les ponga niveles.
- `cron_watchdog.py` — cada 20 min comprueba que las 4 líneas del bot sigan en el crontab, que el python
  del bot conserve Pillow y que cada flujo haya escaneado dentro del plazo; si algo falla avisa por
  **Telegram con la Bot API directa** (no depende de Hermes) y avisa cuando se recupera. Anti-spam: 1 aviso
  cada 3 h (`--simular-min N` para probar sin esperar).
- ✅ Verificado: las 3 líneas nuevas corren en cron (`logs/cron_tracker.log`, `logs/cron_watchdog.log`) y
  el marcador arranca vacío a propósito — se llena con la próxima alerta real.

## Extras del server (2026-09-21, pedido: «el mejor server de señales»)
- **Contexto de derivados en cada alerta**: `derivados()` + `linea_derivados()` en `signals.py` →
  funding del perpetuo (`/fapi/v1/premiumIndex`) y variación de interés abierto a 24 h
  (`/futures/data/openInterestHist`). Si la API de futuros falla, la línea se omite (nunca rompe la alerta).
  Verificado: `💸 Funding +0.0075%/8h (longs pagan a shorts) · 📦 Interés abierto 24h +7.9%`.
- **Pares propios** (`watchlist.py add|del|list` → `watchlist_extra.json`): se suman SIEMPRE al top por
  volumen en los 4 flujos (valida que el par exista antes de agregarlo). Verificado con alta/baja real.
- **Seguimiento en vivo** (`tracker.py::avisos_seguimiento`): avisa cuando una señal abierta llega al
  **75% del camino al objetivo**, al **75% hacia el stop**, o cuando el precio **invalida la entrada**
  (vuelve sobre ella más de 0.5×ATR). Cada aviso, una sola vez por señal (`.seguimiento_estado.json`).
  Guarda corregida: si la señal ya se resolvió no avisa nada (sin ella salían disparates tipo «1149% del camino»).
- **Glosario** en #glosario (2 mensajes): qué significa divergencia, premium/normal, puntaje, ADX, +DI/−DI,
  ATR, volumen, OBV, entrada/stop/objetivo, R:R, soportes, funding, interés abierto y resultados — con la
  advertencia de que no es asesoría y el recordatorio de mirar el marcador.
- **Contexto de mercado** (`mercado.py` + #contexto-mercado, cron `7,22,37,52`): avisa BTC ≥2.5% en 15m,
  BTC ≥4% en 1h, funding ≥0.05%/8h en BTC/ETH y amplitud (≥8 de 20 pares con ≥4% en 1h). Anti-repetición de 2 h.
- **Niveles medidos para momentum y rebote** (`niveles_long.py`, 20 pares, 3 años, 6 estructuras × 3 ventanas):
  - momentum (n=811, ATR medio 5.5%): stop 1.5×ATR / objetivo 3×ATR, tope 20 velas → **+0.84% por señal**,
    ventanas −1.23% / +1.04% / +1.68%.
  - rebote (n=379, ATR medio 3.2%): stop 1.5×ATR / objetivo 6×ATR, tope 40 velas → **+0.15%**, ventanas
    +0.19% / −0.10% / +0.21%.
  - **Ninguna estructura queda positiva en las 3 ventanas** → se publican como *niveles de referencia*
    (para poder seguir la señal y medirla), con la advertencia en el propio mensaje. Antes decían «sin stop
    medido» y el tracker no podía seguirlas.
- ⛔ **Bloqueado**: roles por interés + menú de reacciones y los candados reales de permisos necesitan
  `MANAGE_ROLES` (verificado 403 otra vez). Falta activar «Gestionar server» + «Gestionar roles»
  en el rol Hermes y lo suba al tope de la lista de roles.

## ¿Más indicadores? Medido (2026-09-21, `candidatas2.py` + `candidatas2_robustez.py`)
333 señales de short con volumen (15 pares, 4h y 1h, ~3 años) · referencia: **−0.16% por señal**.
Se probaron 5 candidatos NUNCA medidos antes (los 7 ya descartados están más arriba):

| candidato | mejor tramo | ventanas (1ª/2ª/3ª) | ¿aguanta? |
|---|---|---|---|
| hora del día | Asia +0.40% (n=69) | −0.00% / +0.60% / +0.55% | no (1ª en cero) |
| ATR relativo (percentil del par) | ATR bajo +0.26% (n=70) | +0.17% / +1.30% / −0.58% | no |
| volumen en dólares | <1 M +0.03% (n=61) | +0.07% / −0.15% / −0.46% | no |
| fin de semana | **+0.66%** (n=68) | +0.55% / +0.31% / +0.23% | **sí** |
| BTC en la misma ventana | BTC baja >1% → +0.49% (n=113) | +0.10% / +0.74% / +0.57% | **sí (pero lookahead)** |

- **Los dos «indicadores» clásicos nuevos (ATR relativo y volumen en dólares) no aguantan**: 0 de 2.
- **El corte de BTC usa el futuro** (movimiento de BTC *después* de la señal): es atribución, no filtro.
  Y explica lo importante: **la ventaja de la divergencia bajista es beta** — con BTC subiendo en la misma
  ventana la señal rinde −0.92% (negativo en las 3 ventanas); con BTC bajando, +0.49%. No hay edge propio.
- **Robustez de los dos cortes de contexto** (lo único utilizable en vivo, porque se conoce al alertar):
  - **sesión americana (16-23 UTC = 12-19 h Bolivia): −0.74%**, mitades −0.98% / −0.57%, solo 3 de 15 pares
    positivos → consistentemente mala. La peor combinación (laborable + América): **−1.12%**.
  - **fin de semana: +0.66%**, mitades +0.32% / +0.97%, 8 de 13 pares → prometedor pero con n chico.
  - Costo de cada corte: quitar la sesión americana deja **67%** de las alertas y sube el promedio a
    **+0.13%**; quitar los laborables deja **20%** y sube a **+0.66%**.
- Recomendación: **etiquetar, no filtrar** (se prioriza el volumen de señales): que la alerta diga en qué
  contexto horario cayó y qué rindió ese contexto, en vez de esconder un tercio de las señales.
- ⚠️ Con ~40 combinaciones probadas en el proyecto, parte de esto puede ser múltiple-testing: los dos cortes
  de contexto tienen que re-validarse cuando el marcador propio acumule muestra.
- **Implementado a partir de esto** (lo único que se agregó, sin esconder señales): `contexto_horario()` en
  `signals.py` agrega a cada alerta `🕐 Contexto: sesión X · medido ±Y% por señal (n=Z)`, con aviso cuando el
  contexto es el flojo (América). Y `registrar()` guarda `hora`/`dia` de cada señal enviada, así el
  **resumen semanal del tracker** desglosa por sesión en cuanto haya ≥5 cierres por sesión (re-validación con
  datos propios en vez de confiar en el backtest).
- 🔧 Dos arreglos del tracker en la misma pasada: `--dry-run` **ya no escribe** `results.jsonl` ni el estado
  de avisos (antes una prueba consumía un aviso real: el de NEARUSDT 4h se perdió en una corrida de prueba y
  hubo que repetirla), y el estado de avisos no se guarda en modo prueba.

## Bloques de orden (2026-09-22, pedido)
`orderblocks.py` — zonas de oferta y demanda. Definición documentada (hay varias en el mercado):
bloque = **la última vela contraria antes de un desplazamiento** ≥1.5×ATR en ≤3 velas que además
rompe el extremo de esa vela; **zona = el cuerpo** (open→close); estado `fresco` / `mitigado` / `roto`
(los rotos se descartan: si el precio cerró del otro lado, la zona dejó de valer).

- En la alerta: `🧱 Oferta arriba 86.490–86.620 (a +8.79%) · mitigado · desplazamiento 2.15×ATR` y
  `🧱 Demanda abajo ... · fresco`; si el precio está dentro de un bloque de oferta, lo dice.
- En el gráfico: zonas sombreadas (rojo oferta / verde demanda) desde su vela hasta el borde derecho,
  con la etiqueta y el estado. Los bloques solo entran en el rango visible si están a ≤12% (si no,
  aplastan el gráfico). Se guardan en `signals_log.jsonl` (`ob_arriba`/`ob_abajo`).
- ⚠️ **Medido y en contra de la intuición** (`bloques_estudio.py`: 333 señales de short con volumen,
  15 pares, 4h+1h, ~3 años, 3 ventanas; referencia del conjunto −0.16% por señal):

| caso | n | % por señal | ventanas |
|---|---|---|---|
| **estando DENTRO de un bloque de oferta** | 53 | **−0.49%** | −0.30 / −1.20 / −0.57 (peor que el promedio) |
| oferta fresca ≤3% arriba | 125 | −0.33% | −0.18 / −0.73 / +0.00 |
| demanda fresca ≤3% abajo | 103 | −0.14% | +0.22 / +0.10 / −0.66 |
| sin oferta fresca cerca | 208 | −0.05% | −0.28 / +0.09 / −0.31 |

  Ningún caso es positivo en las 3 ventanas y **estar dentro de un bloque de oferta empeora** la señal.
  Por eso el bloque se publica como **contexto con su número medido al lado** (y la alerta avisa
  "peor que el promedio"), no como una ventaja.

## Auditoría del sistema (2026-09-22) — `salud.py`
Chequeo completo en un comando; **el código de salida es el nº de fallos** (0 = sano), así que sirve
para cron o para preguntar «¿está todo bien?» en cualquier momento. Verifica:
1. las 10 líneas de cron (9 del bot + el vigilante del gateway) y que `cron` esté *enabled*
2. frescura y errores de los 7 logs (cada uno con su plazo máximo esperado)
3. que todos los `.py` compilen y que los módulos importen juntos
4. escaneo en seco de 3 flujos **comprobando que no envían** (cuenta `enviado ok=` antes y después)
5. los 3 gráficos (alerta, cierre con resultado, marcador)
6. validez de los JSON de estado, consistencia (`cierres sin señal de origen`, `duplicados`,
   señales abiertas)
7. tokens de Telegram y Discord y acceso a los canales de destino
8. gateway activo + latido, disco, RAM y carga
9. que el kit de migración se pueda empaquetar y no esté viejo

Primera corrida: **3 hallazgos, todos corregidos** →
- ❌ **los logs del vigilante y del contexto de mercado no tenían hora** (`print` sin fecha): no se
  podía auditar la frescura de sus corridas. Ahora imprimen con `YYYY-MM-DD HH:MM:SS` (verificado en
  las corridas del cron del 15:20 y 15:22).
- ❌ **un cierre duplicado** en `results.jsonl`: el mismo `par|TF|pivote` salió por **dos flujos**
  (premium y normales), así que el tracker lo resolvió dos veces. `marcador()` ahora **cuenta cada
  clave una sola vez** (13 cierres únicos de 14 archivos) — si no, el marcador infla el n.
- ⚠️ el paquete de migración guardado tenía 6 h (el código había cambiado desde que se empaquetó):
  se regeneró. Regla: **regenerar el paquete justo antes de migrar**, no confiar en uno viejo.

Segunda corrida: **56 OK · 0 avisos · 0 fallos** (16 señales abiertas esperando cierre).

## Gráficos en los resultados (2026-09-22, pedido: «mostrá el gráfico y qué señales dieron mal»)
- `chart.render(..., resultado=...)`: el mismo gráfico de la alerta, con el cierre marcado —
  banda superior con el veredicto y el % (color según objetivo/stop), **recorrido entrada→salida**,
  marcador en la vela de salida y **velas posteriores atenuadas** (el "después" ya no cuenta).
- `chart.render_marcador(resultados)`: vista de conjunto, **una barra por señal cerrada** (verde
  objetivo / rojo stop, altura = % de movimiento) con el resumen (n, % al objetivo, % por alerta).
  `tracker.py --resumen` la adjunta en el marcador semanal.
- `tracker.py` adjunta el gráfico del cierre en cada publicación (y `registrar()` en `signals.py`
  ahora guarda `px_prev/px_now/rsi_prev/rsi_now/pivot_gap` para que ese gráfico pueda dibujar la
  divergencia; las señales registradas antes de este cambio no los tienen).
- ⚠️ **Trampa de PIL**: los emojis **no existen en DejaVuSans** → salían como cuadraditos (□) en la
  banda y en la leyenda. Solución: nada de emojis en el PNG; los marcadores se **dibujan** (aspa,
  tilde, punto) y el texto va en palabras ("STOP", "OBJETIVO").
- ⚠️ La banda del veredicto choca con las etiquetas de nivel de la derecha: con `resultado` el
  apilado arranca en `y0 + 56`, no en `y0 + 4`.
- ✅ Verificado en producción: cierre real publicado en #resultados-señales **con su PNG adjunto**
  (1280×830, 48 KB), y los cierres publicados antes de este cambio se ven sin adjunto.

## Migración a un host sin VPS (2026-09-22) — kit listo y probado
Camino elegido: **Google Cloud e2-micro "Always Free"** (2 vCPU compartidas, 1 GB RAM, 30 GB standard,
us-central1/us-west1/us-east1, sin vencimiento y sin política de instancia ociosa). Se muda **solo el
bot de señales** (el gateway de Hermes se queda: usa ~550 MB de RAM y el e2-micro tiene 1 GB).

Kit en `migracion/` (todo probado en un host simulado `/tmp/host_nuevo`):
- `empaquetar.sh` → `bot_senales_<fecha>.tar.gz` (90 KB, 38 archivos) con scripts + **estado** (json y
  **jsonl**) + `NOTAS.md`. **Nunca** incluye `.env`, `cache/`, `venv/`, `charts/` ni `logs/`.
  El manifiesto se verifica **contra el contenido del paquete** (`tar -tzf | grep -qx`), no contra el
  disco: la primera versión afirmaba incluir el estado y no lo hacía (los `.jsonl` no matchean `*.json`).
- `instalar.sh --destino DIR [--paquete X] [--env F] [--sin-cron]` → comprueba Python 3.10+, crea el
  venv con **Pillow** (uv si está, si no `python3 -m venv` + pip), copia código y estado, carga el
  `.env`, instala el cron y verifica. Idempotente.
- `instalar-cron.sh [DESTINO]` → escribe las 9 líneas con las rutas del host nuevo, respalda el
  crontab anterior y evita duplicar líneas viejas del bot.
- `verificar.sh [DESTINO] [--enviar]` → Python/Pillow, scripts, claves, conectividad (Binance, futuros,
  Telegram, Discord), **escaneo en seco de los 3 flujos comprobando que no envía**, tracker, vigilante
  y (opcional) un mensaje real a #hermes. Salida con código = nº de fallos.
- `GUIA_GCP.md` → pasos exactos (región/tipo de disco/firewall), alerta de presupuesto, cambio de mando
  sin alertas duplicadas y rollback.
- `llave_migracion` / `llave_publica.txt` → par de llaves para que el agente haga la migración por SSH.

✅ Verificado: instalación limpia en `/tmp/host_nuevo` → 23 scripts + 12 json, Pillow 12.3.0, 4/4
conectividades HTTP 200, los 3 flujos escanean en seco sin enviar, tracker leyendo 16 señales
pendientes del estado migrado, y **entrega real a Discord** desde esa instalación.
- Detalles de la mudanza que importan: el rebote sigue cada 5 min (el e2-micro lo aguanta);
  los `.env` no viajan en el paquete; el egress del tier (1 GB/mes) cubre de sobra nuestras alertas
  (~40 MB/mes) porque lo que se baja de Binance es ingress y no se cobra.


## Testeo previo a GitHub (2026-09-22)
Batería antes de publicar el repo (queda en el repo para repetirla):
- `tests_sistema.py` — invariantes de las señales (stop/objetivo del lado correcto, riesgo en rango,
  puntaje 1-10, banda con rango válido, entrada no anterior al pivote), **textos sin `None`/`nan`/`e-0`**,
  casos borde (40 velas → lista vacía, señal sin campos opcionales → gráfico igual, señal sin stop →
  `sin_niveles`, **estado corrupto → arranca de cero**), precio minúsculo forzado (PEPE a 4,99e-06 →
  formateado a mano sin notación científica) y que el plan de GitHub corra los 6 flujos. → **0 fallos**.
- `dev/prueba_clon_limpio.py` — **clona el repo**, crea el entorno desde cero, comprueba que no haya
  secretos versionados, corre los 3 horarios del workflow **en seco** verificando que no modifiquen
  ningún archivo (nada de commits basura) y hace un **envío real a Discord**. → **todo correcto**.
- `salud.py --datos` → **58 OK · 0 avisos · 0 fallos**.
Bugs que encontró esta tanda y quedaron corregidos:
- **Rutas absolutas `...` en 5 scripts** de estudio/control: rompían en GitHub. Ahora todas
  se calculan desde `__file__` (el repo quedó portable).
- Al arreglarlas, `verifica_datos.py` y `verifica_temporalidad.py` quedaron usando `os.path` **sin
  importar `os`** → la propia auditoría lo detectó ("sin la línea esperada") y se corrigió. Lección:
  después de tocar rutas/imports de un script, correrlo, no solo compilarlo.
- El chequeo de YAML tropieza con la trampa clásica: `on:` se parsea como **`True`** en YAML 1.1
  (hay que leer `w.get('on', w.get(True))`).

## Pendiente (paso 3)
- Walk-forward con 3 años / datos out-of-sample.
- Sensibilidad de umbrales (RSI 2→1.5 pts, precio 0.5%→1%, volumen 1.2×→1.5×).
- Decidir el alcance del bot: ¿solo 4h (única rentable) o las 4 temporalidades?

## Límites honestos
- 1 año y un solo régimen de mercado; se probaron 24 combinaciones TF×variante, así que parte del 4h+volumen puede ser azar (n=60 es chico).
- Sin walk-forward ni out-of-sample: el siguiente paso para confiar en el 4h es validar en 3 años con datos separados.
- Comisiones asumidas: taker spot 0.10 %. En futuros (0.05 %), el equilibrio del 4h baja a 41.4 %.

## Pendientes
- Validación walk-forward (3 años) del 4h + volumen.
- Decidir si el bot se limita a 4h (única con ventaja medida) o mantiene las 4 temporalidades.
- `data/trading/` no está en git; sin respaldo externo.

## Reloj externo — el bot ya no depende de la VPS (2026-10-06)
El bot corre en **GitHub Actions** (repo público `COEFR/hermes`, workflow `bot.yml`, $0) y el **reloj** es un trabajo de **cron-job.org** (cada 15 min, zona America/La_Paz) que dispara por API:
`POST /repos/COEFR/hermes/actions/workflows/bot.yml/dispatches` · cuerpo `{"ref":"main"}` · encabezado `Authorization: Bearer <PAT fine-grained>`.
- Trampas medidas del endpoint: **GET → 404** (parece «URL mal escrita»), **POST sin cuerpo → 422**, sin encabezado o sin la palabra `Bearer` → **401**, token en la URL (`?access_token=`) → **401**. Con POST + cuerpo + Bearer → **204**.
- En el panel de cron-job.org el token va en **Encabezados** (`Authorization`); el bloque *«Requiere autenticación HTTP»* (usuario/contraseña) es Basic y GitHub lo rechaza. Método y cuerpo, en la sección *Avanzado*.
- Verificado end-to-end: prueba manual 02:53:30 → corrida **#107 `success`** (6/6 flujos, `data-api.binance.vision`, 49 pendientes revisadas); con el **reloj de la VPS apagado**, la **#108 entró sola a las 03:00:12 y terminó `success`** → el reloj externo manda ✓.
- La VPS quedó con **una sola línea de cron** (vigilante del gateway de Hermes): el bot no escanea, no envía y no participa.
- Rollback: `migracion/crontab_con_reloj_20261005_2254.txt` → `crontab <archivo>` devuelve el reloj temporal.
- Pendientes: **(1) vigilancia del silencio** — nadie avisa si cron-job.org deja de disparar (`cron_watchdog.py` miraba logs locales y ya no está agendado); propuesta: chequeo en la VPS que consulte la API de GitHub y avise por Telegram si la última corrida tiene más de ~40 min. **(2) rotar el token**, que quedó en el historial del chat.

## Arreglos del 2026-10-06 (post-migración)
- **Bug corregido — reporte diario duplicado**: el `plan` del ciclo sumaba `tareas_del_dia()` sin deduplicar, así que cuando el programador de GitHub disparaba el mismo horario (`0 12 * * *` o `0 13 * * 1`) el flujo salía **dos veces** en el mismo ciclo (reproducido: `['reporte diario', 'reporte diario']`). Ahora `plan = list(dict.fromkeys(plan + extra))`.
- **`salud.py` consciente de la arquitectura**: detecta que el bot ya no corre en la VPS (`EN_GITHUB`) y, en vez de fallar por los 9 crons y los logs locales, audita el lado de GitHub: antigüedad y conclusión de la última corrida, corridas fallidas, **quién la disparó** (si fue el programador de GitHub, el reloj externo puede estar caído), antigüedad del último commit de estado y del informe del runner. Pasó de **17 fallos falsos** a **41 OK · 2 avisos · 0 fallos**.
- **Vigilante del silencio** (`vigilante_silencio.py`, cron cada 10 min en la VPS): avisa por Telegram si la última corrida tiene más de 45 min, si fallan 3 seguidas, o si el disparo vino del programador de GitHub; anti-spam de 2 h y aviso de recuperación. Probado: `--seco` (no manda), `--tope 1` (detecta el silencio), `--probar-aviso` (manda un «escribiendo…» invisible, sin ruido). Log en `logs/cron_vigilante.log`.
- **Hora del reporte diario — DECIDIDO (2026-10-06)**: se mantiene el horario de siempre, **12:00 Bolivia = 16:00 UTC** para el reporte diario y **lunes 13:00 Bolivia = 17:00 UTC** para el marcador. Los umbrales viven en `tareas_del_dia()` (`hora >= 16`, `dia == 0 and hora >= 17`) y los horarios `0 12`/`0 13 * * 1` se **sacaron del `schedule` del workflow**: con el programador de GitHub atrasado (1-9 h) podían disparar el reporte a cualquier hora; ahora solo la lógica por hora decide. Casos probados: 12 UTC → nada · 15 UTC → nada · 16 UTC → reporte · otra vez el mismo día → nada · lunes 17 UTC → marcador.
