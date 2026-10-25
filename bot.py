"""
Como Estamos - bot de sentimiento BTC
Reporte diario: precio, estructura, sentimiento (noticias + Fear & Greed),
posicionamiento (longs vs shorts) y expectativas de la gente.
Los domingos (cierre de semana) envía además el resumen semanal.
"""

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from estructura import analizar_estructura, preparar_velas, texto_estructura
from noticias import analizar_noticias, obtener_titulares, sentimiento_combinado, texto_noticias
from posicionamiento import obtener_posicionamiento, sesgo_multitud, texto_posicionamiento
from expectativas import obtener_expectativas, sesgo_expectativas, texto_expectativas
from semanal import texto_semanal

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "btc-sentiment-bot"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


FUENTE = "Kraken"  # se actualiza con la fuente que respondió
UN_DIA = 86400


def solo_cerradas(velas):
    """Quita la vela del día en curso: solo velas cuyo día ya terminó."""
    ahora = datetime.now(timezone.utc).timestamp()
    return [v for v in velas if int(v[0]) + UN_DIA <= ahora]


def velas_bitget():
    """Velas diarias de BTCUSDT perpetuo en Bitget, con cierre diario en UTC (igual que TradingView)."""
    base = "https://api.bitget.com/api/v2/mix/market/candles?symbol=BTCUSDT&productType=usdt-futures&limit=500&granularity="
    ultimo_error = None
    for gran in ("1Dutc", "1D"):  # 1Dutc = vela diaria que cierra a las 00:00 UTC
        try:
            data = get_json(base + gran)
            if data.get("code") == "00000" and data.get("data"):
                velas = [[int(v[0]) // 1000, v[1], v[2], v[3], v[4]] for v in data["data"]]
                velas.sort(key=lambda v: v[0])  # de la más vieja a la más nueva
                return velas
            ultimo_error = data.get("msg", data)
        except Exception as e:
            ultimo_error = e
    raise RuntimeError(f"Bitget error: {ultimo_error}")


def velas_kraken():
    """Respaldo: velas diarias de BTC/USD al contado en Kraken."""
    data = get_json("https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval=1440")
    if data.get("error"):
        raise RuntimeError(f"Kraken error: {data['error']}")
    resultado = data["result"]
    clave = next(k for k in resultado if k != "last")
    return [v[:5] for v in resultado[clave]]


def velas_diarias_btc():
    """Intenta Bitget primero; si falla, usa Kraken. Devuelve solo velas cerradas."""
    global FUENTE
    try:
        velas = solo_cerradas(velas_bitget())
        if len(velas) < 50:
            raise RuntimeError("Bitget devolvió muy pocas velas")
        FUENTE = "Bitget BTCUSDT perpetuo"
    except Exception as e:
        print("Bitget no disponible, uso Kraken:", e)
        velas = solo_cerradas(velas_kraken())
        FUENTE = "Kraken BTC/USD (respaldo)"
    return velas


def fear_and_greed(dias=7):
    """Devuelve [(valor, texto)] de los últimos `dias`, del más viejo al más nuevo."""
    data = get_json(f"https://api.alternative.me/fng/?limit={dias}")
    return [(int(d["value"]), d["value_classification"]) for d in reversed(data["data"])]


def _enviar_un_mensaje(texto):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": texto,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def enviar_telegram(texto, limite=3900):
    """Telegram acepta máximo 4096 caracteres: si es más largo, lo parte por bloques."""
    bloques, actual = [], ""
    for parte in texto.split("\n\n"):
        if actual and len(actual) + len(parte) + 2 > limite:
            bloques.append(actual)
            actual = parte
        else:
            actual = f"{actual}\n\n{parte}" if actual else parte
    bloques.append(actual)
    respuesta = {}
    for b in bloques:
        respuesta = _enviar_un_mensaje(b[:4096])
    return respuesta


def reporte_diario(velas, est, fg_hist, pos, sesgo_pos):
    ultima, anterior = velas[-1], velas[-2]
    fecha = datetime.fromtimestamp(int(ultima[0]), tz=timezone.utc).strftime("%d %b %Y")
    cierre, maximo, minimo = float(ultima[4]), float(ultima[2]), float(ultima[3])
    cambio = (cierre / float(anterior[4]) - 1) * 100

    fg_valor, fg_texto = fg_hist[-1]
    titulares, fallidas = obtener_titulares(horas=24)
    noticias = analizar_noticias(titulares)
    combinado = sentimiento_combinado(fg_valor, noticias["puntaje"])
    exp = obtener_expectativas(cierre, titulares)
    sesgo_exp = sesgo_expectativas(exp)

    flecha = "🟢" if cambio >= 0 else "🔴"
    return (
        f"<b>Como Estamos</b>\n"
        f"Vela del {fecha}\n\n"
        f"₿ Cierre: <b>${cierre:,.0f}</b> {flecha} {cambio:+.2f}%\n"
        f"Máximo: ${maximo:,.0f} | Mínimo: ${minimo:,.0f}\n\n"
        f"{texto_estructura(est)}\n\n"
        f"{texto_noticias(noticias, combinado, fg_valor, fg_texto, fallidas)}\n\n"
        f"{texto_posicionamiento(pos, sesgo_pos)}\n\n"
        f"{texto_expectativas(exp, sesgo_exp)}\n\n"
        f"<i>Fuente: {FUENTE}</i>"
    )


def reporte_semanal(velas, est, fg_hist, pos):
    titulares_sem, _ = obtener_titulares(horas=24 * 7)
    noticias_sem = analizar_noticias(titulares_sem, n=5)
    cierre = float(velas[-1][4])
    exp = obtener_expectativas(cierre, titulares_sem)
    return texto_semanal(velas, est, fg_hist, noticias_sem, pos,
                         texto_expectativas(exp, sesgo_expectativas(exp)))


def main():
    # MODO: "diario", "semanal" o "auto" (auto = diario, y además semanal si cerró el domingo)
    modo = (os.environ.get("MODO") or "auto").strip().lower()

    velas = velas_diarias_btc()
    est = analizar_estructura(preparar_velas(velas))
    fg_hist = fear_and_greed(7)
    pos = obtener_posicionamiento()
    sesgo_pos = sesgo_multitud(pos)

    domingo_cerrado = datetime.fromtimestamp(int(velas[-1][0]), tz=timezone.utc).weekday() == 6

    if modo in ("auto", "diario"):
        r = enviar_telegram(reporte_diario(velas, est, fg_hist, pos, sesgo_pos))
        print("Diario:", "OK" if r.get("ok") else r)
    if modo == "semanal" or (modo == "auto" and domingo_cerrado):
        r = enviar_telegram(reporte_semanal(velas, est, fg_hist, pos))
        print("Semanal:", "OK" if r.get("ok") else r)


if __name__ == "__main__":
    main()
