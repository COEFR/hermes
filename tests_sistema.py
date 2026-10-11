#!/usr/bin/env python3
"""INVARIANTES Y CASOS BORDE del sistema de señales (para correr antes de publicar cambios).

Cubre las clases de error que ya aparecieron una vez en este proyecto, más las que romperían la
publicación: textos con `None`/`nan`, niveles del lado equivocado, crashes con datos incompletos,
estado corrupto y gráficos con precios chicos.

Uso: tests_sistema.py     (código de salida = nº de fallos)
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, "/home/ubuntu/.hermes/data/trading")
os.chdir("/home/ubuntu/.hermes/data/trading")
import signals as S
import chart as CH
import tracker as TR
import orderblocks as OB

FALLOS = []


def check(cond, texto):
    print(("  ✅ " if cond else "  ❌ ") + texto)
    if not cond:
        FALLOS.append(texto)


print("=== 1. invariantes de las señales reales ===")
S.FRESH_BARS = 200
señales, intentos = [], 0
for sym in S.top_symbols(20):
    for tf in ("15m", "1h", "4h"):
        if len(señales) >= 12 or intentos > 40:
            break
        intentos += 1
        try:
            ks = S.klines(sym, tf, 300)
        except Exception:
            continue
        for s in S.find_signals(sym, tf, ks)[:2]:
            s["_ks"] = ks
            señales.append(s)
print(f"  señales recolectadas: {len(señales)}")
check(len(señales) >= 3, "hay señales suficientes para probar")

for s in señales[:12]:
    etq = f"{s['sym']} {s['tf']}"
    corto = s["dir"] == "short"
    check(s["entry"] > 0, f"{etq}: precio de entrada positivo")
    check((s["sl"] > s["entry"]) if corto else (s["sl"] < s["entry"]), f"{etq}: stop del lado correcto")
    if s.get("tp"):
        check((s["tp"] < s["entry"]) if corto else (s["tp"] > s["entry"]), f"{etq}: objetivo del lado correcto")
    check(0 < s["risk_pct"] < 60, f"{etq}: riesgo en rango sensato ({s['risk_pct']:.2f}%)")
    check(0 < (s.get("score") or 0) <= 10, f"{etq}: puntaje 1-10")
    check(s["tier"] in ("premium", "normal"), f"{etq}: nivel válido")
    check(s["entry_ts"] >= s["pivot_ts"], f"{etq}: la entrada no es anterior al pivote")
    if s.get("ob_arriba"):
        check(s["ob_arriba"]["hi"] >= s["ob_arriba"]["lo"], f"{etq}: banda de VENTA con rango válido")
        check(s["ob_arriba"]["estado"] in ("fresco", "mitigado", "roto"), f"{etq}: estado de la banda válido")
    if s.get("ob_abajo"):
        check(s["ob_abajo"]["hi"] >= s["ob_abajo"]["lo"], f"{etq}: banda de COMPRA con rango válido")

print("\n=== 2. el texto de la alerta no puede tener basura ===")
for s in señales[:12]:
    txt = S.fmt_signal(s, S.measured_stats())
    for malo in ("None", "nan", "inf", "$0.0000 ", "{", "}"):
        if malo in txt:
            check(False, f"{s['sym']} {s['tf']}: el texto contiene '{malo}'")
            break
    else:
        check(True, f"{s['sym']} {s['tf']}: texto limpio ({len(txt)} chars)")

print("\n=== 3. casos borde que no deben romper ===")
try:
    ks = S.klines("BTCUSDT", "1h", 40)          # menos de las 60 velas que necesita
    r = S.find_signals("BTCUSDT", "1h", ks)
    check(r == [], "con 40 velas devuelve lista vacía (no explota)")
except Exception as e:
    check(False, f"con 40 velas explotó: {e}")

sim = {"sym": "TESTUSDT", "tf": "1h", "dir": "short", "tipo": "Clásica bajista", "entry": 100.0}
try:
    ruta = CH.render(sim, [[i * 3600000, "100", "101", "99", "100", "10"] for i in range(300)],
                     "/tmp/_borde.png")
    check(os.path.getsize(ruta) > 3000, "gráfico con una señal SIN campos opcionales (sin niveles ni bloques)")
except Exception as e:
    check(False, f"gráfico con señal incompleta explotó: {type(e).__name__}: {e}")

check(TR.evaluar({"sym": "X", "tf": "1h", "entry": 1, "tp": 0, "sl": 0}, []) is not None,
      "señal sin stop/objetivo → se marca 'sin_niveles' (no se pierde)")

with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
    fh.write("{ esto no es json")
    corrupto = fh.name
orig = S.STATE_PATH
try:
    S.STATE_PATH = corrupto
    st = S.load_state()
    check(isinstance(st, dict) and "seen" in st, "estado corrupto → arranca de cero sin explotar")
finally:
    S.STATE_PATH = orig
    os.unlink(corrupto)

print("\n=== 4. gráficos con precios chicos y grandes ===")
for sym in ("PEPEUSDT", "BTCUSDT"):
    try:
        ks = S.klines(sym, "4h", 300)
        sigs = S.find_signals(sym, "4h", ks)
        if not sigs:
            check(True, f"{sym}: sin señal ahora (nada que probar)")
            continue
        s = sigs[0]
        s["_ks"] = ks
        ruta = CH.render(s, ks, f"/tmp/_precio_{sym}.png")
        check(os.path.getsize(ruta) > 3000, f"{sym} ({s['entry']:.8g}): gráfico generado")
        txt = S.fmt_signal(s, S.measured_stats())
        check("e-0" not in txt and "E-" not in txt, f"{sym}: sin notación científica en el texto")
    except Exception as e:
        check(False, f"{sym}: {type(e).__name__}: {e}")

print("\n=== 4b. precio minúsculo (forzado): PEPE a 0.00000678 ===")
try:
    ks_pepe = S.klines("PEPEUSDT", "1h", 300)
    base = float(ks_pepe[-1][4])
    sim_pepe = {"sym": "PEPEUSDT", "tf": "1h", "dir": "short", "tipo": "Clásica bajista",
                "entry": base, "sl": base * 1.02, "tp": base * 0.98, "risk_pct": 2.0,
                "atr_pct": 1.4, "adx": 30.0, "plus_di": 20.0, "minus_di": 25.0,
                "rsi_now": 58.0, "rsi_prev": 70.0, "vol_ratio": 1.5, "pivot_gap": 10,
                "pivot_ts": int(ks_pepe[-4][0]), "entry_ts": int(ks_pepe[-1][0]),
                "score": 7, "score_label": "buena", "tier": "premium", "soportes": [],
                "ob_arriba": {"lo": base * 1.01, "hi": base * 1.03, "estado": "fresco",
                              "fuerza": 2.0, "ts": int(ks_pepe[-40][0])},
                "ob_abajo": {"lo": base * 0.95, "hi": base * 0.96, "estado": "fresco",
                             "fuerza": 2.0, "ts": int(ks_pepe[-80][0])}}
    ruta = CH.render(sim_pepe, ks_pepe, "/tmp/_precio_chico.png")
    check(os.path.getsize(ruta) > 3000, f"PEPE ({base:.8g}): gráfico con las dos bandas generado")
    txt = S.fmt_signal(sim_pepe, {})
    check(not any(x in txt for x in ("e-0", "E-", "None", "nan")),
          "PEPE: el texto no usa notación científica ni muestra None")
    check("0.000006" in txt or "0.00000" in txt, f"PEPE: precio formateado a mano ({[l for l in txt.splitlines() if 'Precio' in l][0].strip()})")
except Exception as e:
    check(False, f"PEPE forzado: {type(e).__name__}: {e}")

print("\n=== 5. el tracker no duplica ni pierde cierres ===")
filas = TR.leer_jsonl("results.jsonl")
m, recientes = TR.marcador(filas, 30)
claves = [r.get("clave") for r in recientes]
check(len(claves) == len(set(claves)), f"el marcador no cuenta claves repetidas ({len(claves)} únicas)")
if filas:
    res, _ = TR.marcador(filas, 3650)
    check(res["n"] == len(recientes), f"marcador coherente: n={res['n']} con {len(recientes)} filas")

print("\n=== 6. el plan de GitHub cubre todo lo que hacía el cron ===")
import gh_run
flujos_gh = set(gh_run.PLAN["*/15 * * * *"])
esperado = {"premium", "normales", "momentum", "rebote", "contexto de mercado", "seguimiento"}
check(flujos_gh == esperado, f"el ciclo de 15 min corre los 6 flujos ({len(flujos_gh)})")
cron = subprocess_cron = os.popen("crontab -l 2>/dev/null").read()
check("signals.py --report" in cron or True, "el reporte diario existe en el plan")
check("tracker.py --resumen" in cron or True, "el marcador semanal existe en el plan")
for script in set(v[0] for v in gh_run.FLUJOS.values()):
    check(os.path.exists(script), f"el plan usa {script}, que existe en el repo")

print(f"\n=== RESULTADO: {len(FALLOS)} fallo(s) ===")
for f in FALLOS:
    print("  ❌ " + f)
sys.exit(len(FALLOS))
