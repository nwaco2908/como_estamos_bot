"""
Como Estamos - Backtest histórico del régimen de fondo y del escenario diario.

Simula día por día (desde 2018) lo que el bot habría dicho usando SOLO la información
disponible ese día, y luego mide qué hizo el precio después.

Qué se puede simular con datos históricos gratuitos:
  ✅ Precio y estructura (velas diarias)
  ✅ Fear & Greed (desde febrero 2018)
  ❌ Noticias, macro, posicionamiento, Polymarket (no hay historia gratuita):
     en el backtest el "miedo/optimismo" sale solo del Fear & Greed.

Qué mide para cada señal (contando solo el PRIMER día de cada episodio):
  - rendimiento promedio y mediano a 7, 14 y 30 días
  - % de veces que el precio subió
  - todo comparado contra "un día cualquiera" DEL MISMO RÉGIMEN (no contra el promedio general,
    que mezcla mercados alcistas y bajistas)
También prueba el puntaje "precio vs. noticias" (absorción): en días de miedo, ¿sirve de algo
que el precio haya absorbido las malas noticias?

Uso (en GitHub Actions): python backtest.py  → imprime el informe y lo envía a Telegram.
"""

import json
import os
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

from ciclo import analizar_ciclo
from escenarios import absorcion, evaluar
from estructura import analizar_estructura, preparar_velas

VENTANA = 500           # velas que mira el bot cada día (igual que en vivo)
HORIZONTES = (7, 14, 30)
DIA = 86400

from senales import SESGO_ESC, SESGO_FASE  # misma tabla de sesgos que el marcador semanal


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "como-estamos-backtest"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ───────────────────────── DATOS ─────────────────────────
def velas_binance():
    """BTCUSDT diario desde 2017 (endpoint público de datos de Binance, accesible desde EE. UU.)."""
    velas, inicio = [], 1502928000000  # 17 ago 2017
    for _ in range(10):
        d = _get("https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT&interval=1d"
                 f"&limit=1000&startTime={inicio}")
        if not d:
            break
        velas += [[int(k[0]) // 1000, k[1], k[2], k[3], k[4]] for k in d]
        inicio = int(d[-1][0]) + DIA * 1000
        if len(d) < 1000:
            break
    return velas


def velas_kraken():
    d = _get("https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval=1440&since=1483228800")
    r = d["result"]
    return [v[:5] for v in r[next(k for k in r if k != "last")]]


def obtener_velas():
    for nombre, f in (("Binance", velas_binance), ("Kraken", velas_kraken)):
        try:
            v = f()
            if len(v) > 800:
                print(f"Velas: {len(v)} de {nombre}")
                return v, nombre
        except Exception as e:
            print(f"{nombre} no disponible: {e}")
    raise RuntimeError("Sin datos de precio")


def fear_greed_historico():
    d = _get("https://api.alternative.me/fng/?limit=0")
    return {datetime.fromtimestamp(int(x["timestamp"]), tz=timezone.utc).date(): int(x["value"])
            for x in d["data"]}


# ───────────────────────── SIMULACIÓN ─────────────────────────
def simular(velas, fg_por_fecha):
    ahora = datetime.now(timezone.utc).timestamp()
    velas = [v for v in velas if int(v[0]) + DIA <= ahora]  # solo velas cerradas
    fechas = [datetime.fromtimestamp(int(v[0]), tz=timezone.utc).date() for v in velas]
    primera_fg = min(fg_por_fecha)
    registros = []
    ultimo_fg = 50
    fg_serie = []
    for f in fechas:
        ultimo_fg = fg_por_fecha.get(f, ultimo_fg)
        fg_serie.append(ultimo_fg)

    for i in range(len(velas)):
        if i < 300 or fechas[i] < primera_fg + (fechas[1] - fechas[0]) * 90:
            continue
        ventana = velas[max(0, i - VENTANA + 1):i + 1]
        fg90 = fg_serie[max(0, i - 89):i + 1]
        try:
            c = analizar_ciclo(ventana, fg90)
        except Exception:
            continue
        sent = fg_serie[i] * 2 - 100
        # Escenario diario (versión reducida: sin noticias, posicionamiento ni expectativas)
        esc_titulo = None
        try:
            est = analizar_estructura(preparar_velas(ventana[-200:]))
            fg_hist = [(v, "") for v in fg_serie[max(0, i - 9):i + 1]]
            e = evaluar(ventana[-200:], est, fg_hist, sent, None, None, None, [])
            esc_titulo = e["titulo"]
        except Exception:
            pass
        ab = absorcion(ventana[-20:], sent, None)
        registros.append({"i": i, "fecha": fechas[i], "fase": c["fase"], "regimen": c["regimen"],
                          "escenario": esc_titulo, "absorcion": ab})
    return velas, registros


def medir(velas, registros):
    cierres = [float(v[4]) for v in velas]
    maximos = [float(v[2]) for v in velas]
    minimos = [float(v[3]) for v in velas]
    n = len(velas)
    for reg in registros:
        i, r = reg["i"], {}
        for h in HORIZONTES:
            if i + h < n:
                r[h] = (cierres[i + h] / cierres[i] - 1) * 100
        if i + 14 < n:
            r["max14"] = (max(maximos[i + 1:i + 15]) / cierres[i] - 1) * 100
            r["min14"] = (min(minimos[i + 1:i + 15]) / cierres[i] - 1) * 100
        reg["fut"] = r


def episodios(registros, clave):
    """Agrupa días seguidos con la misma señal: cuenta solo el PRIMER día de cada episodio."""
    out, previo = [], None
    for r in registros:
        s = r[clave]
        if s and s != previo:
            out.append(r)
        previo = s
    return out


def _mediana(vals):
    vals = sorted(vals)
    m = len(vals) // 2
    return vals[m] if len(vals) % 2 else (vals[m - 1] + vals[m]) / 2


def estadisticas(lista):
    """Para cada horizonte: casos, promedio, mediana y % de veces que subió."""
    res = {"n": len(lista)}
    for h in HORIZONTES:
        vals = [r["fut"][h] for r in lista if h in r["fut"]]
        if vals:
            res[h] = {"n": len(vals), "prom": sum(vals) / len(vals), "med": _mediana(vals),
                      "sube": sum(1 for v in vals if v > 0) / len(vals) * 100}
    caidas = [r["fut"]["min14"] for r in lista if "min14" in r["fut"]]
    subidas = [r["fut"]["max14"] for r in lista if "max14" in r["fut"]]
    if caidas:
        res["caida14"], res["subida14"] = sum(caidas) / len(caidas), sum(subidas) / len(subidas)
    return res


def _linea_ref(nombre, s):
    partes = [f"{h} d {s[h]['prom']:+.1f}% (sube {s[h]['sube']:.0f}%)" for h in HORIZONTES if h in s]
    return f"{nombre} (n={s['n']}): " + " | ".join(partes)


def _bloque(nombre, s, ref, flecha=""):
    """Tres líneas por señal: rendimiento, % de subidas y ventaja frente a su referencia."""
    aviso = " ⚠️ pocos casos" if s["n"] < 15 else ""
    lineas = [f"{flecha}<b>{nombre}</b> (n={s['n']}){aviso}"]
    lineas.append("   Rend. prom. (mediana): " + " | ".join(
        f"{h} d {s[h]['prom']:+.1f}% ({s[h]['med']:+.1f}%)" for h in HORIZONTES if h in s))
    lineas.append("   Sube: " + " | ".join(f"{h} d {s[h]['sube']:.0f}%" for h in HORIZONTES if h in s)
                  + (f" · peor caída 14 d {s['caida14']:+.1f}%" if "caida14" in s else ""))
    if ref:
        ventaja = " | ".join(f"{h} d {s[h]['med'] - ref[h]['med']:+.1f} pts, {s[h]['sube'] - ref[h]['sube']:+.0f}% subidas"
                             for h in HORIZONTES if h in s and h in ref)
        lineas.append(f"   Ventaja vs. referencia (mediana): {ventaja}")
    return "\n".join(lineas)


def informe(velas, registros, fuente):
    desde, hasta = registros[0]["fecha"], registros[-1]["fecha"]
    general = estadisticas(registros)
    por_regimen = {}
    for reg in ("alcista", "bajista", "rango"):
        dias = [r for r in registros if r["regimen"] == reg]
        if dias:
            por_regimen[reg] = estadisticas(dias)

    bloques = ["\n".join([
        "<b>Como Estamos — Backtest histórico (v2)</b>",
        f"{desde:%d %b %Y} → {hasta:%d %b %Y} ({len(registros)} días, velas de {fuente})",
        "<i>Miedo/optimismo solo con Fear &amp; Greed (sin noticias ni posicionamiento).</i>",
    ])]
    ref = ["📏 <b>Referencias: un día cualquiera</b>", _linea_ref("Todos los días", general)]
    ref += [_linea_ref(f"Régimen {reg}", s) for reg, s in por_regimen.items()]
    ref.append("<i>Una señal solo tiene ventaja si supera a un día cualquiera de SU régimen.</i>")
    bloques.append("\n".join(ref))

    # ── Fases del régimen: cada una contra los días de su mismo régimen ──
    bloques.append("🌐 <b>Fases del régimen de fondo</b>\n<i>Primer día de cada episodio. "
                   "⬆️/⬇️ = dirección que espera el bot; ➖ = solo informativa.</i>")
    grupos = defaultdict(list)
    for r in episodios(registros, "fase"):
        grupos[(r["fase"], r["regimen"])].append(r)
    filas = []
    for (fase, reg), lista in grupos.items():
        s = estadisticas(lista)
        if s["n"] >= 5 and 14 in s:
            filas.append((s[14]["med"] - por_regimen.get(reg, general)[14]["med"], fase, reg, s))
    for _, fase, reg, s in sorted(filas, key=lambda x: -x[0]):
        flecha = {1: "⬆️ ", -1: "⬇️ ", 0: "➖ "}.get(SESGO_FASE.get(fase, 0), "➖ ")
        bloques.append(_bloque(f"{fase} · régimen {reg}", s, por_regimen.get(reg, general), flecha))

    # ── Escenarios diarios: contra todos los días ──
    bloques.append("🎯 <b>Escenario diario (versión reducida)</b>\n<i>Primer día de cada episodio, "
                   "comparado contra todos los días.</i>")
    grupos = defaultdict(list)
    for r in episodios(registros, "escenario"):
        grupos[r["escenario"]].append(r)
    filas = []
    for nombre, lista in grupos.items():
        s = estadisticas(lista)
        if s["n"] >= 5 and 14 in s:
            filas.append((s[14]["med"] - general[14]["med"], nombre, s))
    for _, nombre, s in sorted(filas, key=lambda x: -x[0]):
        flecha = {1: "⬆️ ", -1: "⬇️ ", 0: "➖ "}.get(SESGO_ESC.get(nombre, 0), "➖ ")
        bloques.append(_bloque(nombre, s, general, flecha))

    # ── Precio vs. noticias (absorción) ──
    bloques.append("🥊 <b>Precio vs. noticias (absorción)</b>\n<i>Todos los días de miedo (F&amp;G ≤ 37) u optimismo "
                   "(F&amp;G ≥ 63), según cómo reaccionó el precio ese día. Aquí se cuentan todos los días, "
                   "no solo el primero de cada episodio.</i>")

    def grupo_abs(tipo, regimen=None):
        dias = [r for r in registros if r["absorcion"] and r["absorcion"]["tipo"] == tipo
                and (regimen is None or r["regimen"] == regimen)]
        return {
            "contra": [r for r in dias if r["absorcion"]["puntaje"] >= 30],
            "mixta": [r for r in dias if -30 < r["absorcion"]["puntaje"] < 30],
            "siguio": [r for r in dias if r["absorcion"]["puntaje"] <= -30],
            "todos": dias,
        }

    casos = (
        ("Días de MIEDO (todos los regímenes)", "malas", None,
         ("Absorbió las malas noticias (≥ +30)", "Reacción mixta", "Siguió a las malas noticias (≤ -30)")),
        ("Días de MIEDO en régimen ALCISTA (el caso de la tesis)", "malas", "alcista",
         ("Absorbió las malas noticias (≥ +30)", "Reacción mixta", "Siguió a las malas noticias (≤ -30)")),
        ("Días de OPTIMISMO (todos los regímenes)", "buenas", None,
         ("No reaccionó a las buenas noticias (≥ +30)", "Reacción mixta", "Siguió a las buenas noticias (≤ -30)")),
    )
    for titulo, tipo, regimen, nombres in casos:
        g = grupo_abs(tipo, regimen)
        if len(g["todos"]) < 20:
            continue
        base = estadisticas(g["todos"])
        bloques.append(f"<b>{titulo}</b>\n{_linea_ref('Referencia: todos esos días', base)}")
        for clave, nombre in zip(("contra", "mixta", "siguio"), nombres):
            if len(g[clave]) >= 10:
                bloques.append(_bloque(nombre, estadisticas(g[clave]), base))

    ult = registros[-1]
    bloques.append(f"📍 Hoy el backtest marca: régimen <b>{ult['regimen']}</b>, fase <b>{ult['fase']}</b>\n"
                   "<i>Ventaja = diferencia de la mediana y del % de subidas frente a la referencia. "
                   "Con pocos casos (menos de 15) una ventaja puede ser casualidad. "
                   "Resultados pasados no garantizan resultados futuros.</i>")
    return "\n\n".join(bloques)


def enviar(texto):
    token, destinos = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token:
        return
    destino = destinos.split(",")[0].strip()  # solo a tu chat personal, no al canal
    mensajes, actual = [], ""
    for parte in texto.split("\n\n"):
        if actual and len(actual) + len(parte) > 3600:
            mensajes.append(actual)
            actual = parte
        else:
            actual = f"{actual}\n\n{parte}" if actual else parte
    mensajes.append(actual)
    for m in mensajes:
        datos = urllib.parse.urlencode({"chat_id": destino, "text": m, "parse_mode": "HTML"}).encode()
        urllib.request.urlopen(urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage", data=datos), timeout=30)


def main():
    velas, fuente = obtener_velas()
    fg = fear_greed_historico()
    print(f"Fear & Greed: {len(fg)} días")
    velas, registros = simular(velas, fg)
    print(f"Días simulados: {len(registros)}")
    medir(velas, registros)
    texto = informe(velas, registros, fuente)
    print(texto)
    with open("backtest_resultado.txt", "w", encoding="utf-8") as f:
        f.write(texto)
    enviar(texto)


if __name__ == "__main__":
    main()
