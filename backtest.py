"""
Como Estamos - Backtest histórico del régimen de fondo y del escenario diario.

Simula día por día (desde 2018) lo que el bot habría dicho usando SOLO la información
disponible ese día, y luego mide qué hizo el precio después.

Qué se puede simular con datos históricos gratuitos:
  ✅ Precio y estructura (velas diarias)
  ✅ Fear & Greed (desde febrero 2018)
  ❌ Noticias, macro, posicionamiento, Polymarket (no hay historia gratuita):
     en el backtest el "miedo/optimismo" sale solo del Fear & Greed.

Uso (en GitHub Actions): python backtest.py  → imprime el informe y lo envía a Telegram.
"""

import json
import os
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

from ciclo import analizar_ciclo
from escenarios import evaluar
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
    for i, f in enumerate(fechas):
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
        # Escenario diario (versión reducida: sin noticias, posicionamiento ni expectativas)
        esc_titulo = None
        try:
            est = analizar_estructura(preparar_velas(ventana[-200:]))
            fg_hist = [(v, "") for v in fg_serie[max(0, i - 9):i + 1]]
            sent = fg_serie[i] * 2 - 100
            e = evaluar(ventana[-200:], est, fg_hist, sent, None, None, None, [])
            esc_titulo = e["titulo"]
        except Exception:
            pass
        registros.append({"i": i, "fecha": fechas[i], "fase": c["fase"], "regimen": c["regimen"],
                          "escenario": esc_titulo})
    return velas, registros


def medir(velas, registros):
    cierres = [float(v[4]) for v in velas]
    maximos = [float(v[2]) for v in velas]
    minimos = [float(v[3]) for v in velas]
    n = len(velas)

    def futuro(i):
        r = {}
        for h in HORIZONTES:
            if i + h < n:
                r[h] = (cierres[i + h] / cierres[i] - 1) * 100
        if i + 14 < n:
            r["max14"] = (max(maximos[i + 1:i + 15]) / cierres[i] - 1) * 100
            r["min14"] = (min(minimos[i + 1:i + 15]) / cierres[i] - 1) * 100
        return r

    for reg in registros:
        reg["fut"] = futuro(reg["i"])

    # Referencia: un día cualquiera
    base = {h: [r["fut"][h] for r in registros if h in r["fut"]] for h in HORIZONTES}
    return base


def episodios(registros, clave):
    """Agrupa días seguidos con la misma señal: cuenta solo el PRIMER día de cada episodio."""
    out, previo = [], None
    for r in registros:
        s = r[clave]
        if s and s != previo:
            out.append(r)
        previo = s
    return out


def estadisticas(lista, sesgo):
    res = {"n": len(lista)}
    for h in HORIZONTES:
        vals = [r["fut"][h] for r in lista if h in r["fut"]]
        if vals:
            res[f"r{h}"] = sum(vals) / len(vals)
            res[f"ok{h}"] = sum(1 for v in vals if v * sesgo > 0) / len(vals) * 100
            res[f"n{h}"] = len(vals)
    adv = [r["fut"]["min14" if sesgo > 0 else "max14"] for r in lista if "min14" in r["fut"]]
    if adv:
        res["adverso"] = sum(adv) / len(adv)
    return res


def _fmt(x, signo=True):
    return f"{x:+.1f}%" if signo else f"{x:.0f}%"


def informe(velas, registros, base, fuente):
    desde, hasta = registros[0]["fecha"], registros[-1]["fecha"]
    lineas = [
        "<b>Como Estamos — Backtest histórico</b>",
        f"{desde:%d %b %Y} → {hasta:%d %b %Y} ({len(registros)} días, velas de {fuente})",
        "<i>Miedo/optimismo solo con Fear &amp; Greed (sin noticias ni posicionamiento).</i>",
        "",
        "📏 <b>Referencia: un día cualquiera</b>",
    ]
    for h in HORIZONTES:
        v = base[h]
        lineas.append(f"{h} días: promedio {_fmt(sum(v) / len(v))} | sube {sum(1 for x in v if x > 0) / len(v) * 100:.0f}% de las veces")

    for titulo, clave, sesgos in (("🌐 <b>Fases del régimen de fondo</b>", "fase", SESGO_FASE),
                                  ("🎯 <b>Escenario diario (versión reducida)</b>", "escenario", SESGO_ESC)):
        lineas += ["", titulo, "<i>Episodios (1er día) · rend. 7/14/30 d · acierto a 14 d · peor movimiento en contra (14 d)</i>"]
        eps = episodios(registros, clave)
        grupos = defaultdict(list)
        for r in eps:
            grupos[r[clave]].append(r)
        filas = []
        for nombre, lista in grupos.items():
            sesgo = sesgos.get(nombre)
            if not sesgo:
                continue
            s = estadisticas(lista, sesgo)
            if s["n"] < 3 or "r14" not in s:
                continue
            filas.append((nombre, sesgo, s))
        filas.sort(key=lambda x: (-x[1], -x[2]["ok14"]))
        for nombre, sesgo, s in filas:
            flecha = "⬆️" if sesgo > 0 else "⬇️"
            partes = " / ".join(_fmt(s[f"r{h}"]) for h in HORIZONTES if f"r{h}" in s)
            muestra = " ⚠️ pocos casos" if s["n"] < 10 else ""
            lineas.append(f"{flecha} <b>{nombre}</b> (n={s['n']}){muestra}")
            lineas.append(f"   {partes} | acierto {_fmt(s['ok14'], False)} | en contra {_fmt(s['adverso'])}")

    # Caso actual
    ult = registros[-1]
    lineas += ["", f"📍 Hoy el backtest marca: régimen <b>{ult['regimen']}</b>, fase <b>{ult['fase']}</b>"]
    lineas += ["", "<i>Acierto = el precio fue en la dirección esperada a 14 días. "
               "Una señal solo tiene ventaja si supera claramente a 'un día cualquiera'. "
               "Resultados pasados no garantizan resultados futuros.</i>"]
    return "\n".join(lineas)


def enviar(texto):
    token, destinos = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token:
        return
    destino = destinos.split(",")[0].strip()  # solo a tu chat personal, no al canal
    bloques, actual = [], ""
    for parte in texto.split("\n\n"):
        if actual and len(actual) + len(parte) > 3800:
            bloques.append(actual)
            actual = parte
        else:
            actual = f"{actual}\n\n{parte}" if actual else parte
    bloques.append(actual)
    for b in bloques:
        datos = urllib.parse.urlencode({"chat_id": destino, "text": b, "parse_mode": "HTML"}).encode()
        urllib.request.urlopen(urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage", data=datos), timeout=30)


def main():
    velas, fuente = obtener_velas()
    fg = fear_greed_historico()
    print(f"Fear & Greed: {len(fg)} días")
    velas, registros = simular(velas, fg)
    print(f"Días simulados: {len(registros)}")
    base = medir(velas, registros)
    texto = informe(velas, registros, base, fuente)
    print(texto)
    with open("backtest_resultado.txt", "w", encoding="utf-8") as f:
        f.write(texto)
    enviar(texto)


if __name__ == "__main__":
    main()
