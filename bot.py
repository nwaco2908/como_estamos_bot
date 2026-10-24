"""
Como Estamos - bot de sentimiento BTC - Fase 3 (estructura)
Envía a Telegram el cierre diario de BTC, la estructura de mercado y el índice Fear & Greed.
"""

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from estructura import analizar_estructura, preparar_velas, texto_estructura

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


def fear_and_greed():
    data = get_json("https://api.alternative.me/fng/?limit=1")
    item = data["data"][0]
    return int(item["value"]), item["value_classification"]


def enviar_telegram(texto):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": texto,
        "parse_mode": "HTML",
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    velas = velas_diarias_btc()
    ultima = velas[-1]
    anterior = velas[-2]

    fecha = datetime.fromtimestamp(int(ultima[0]), tz=timezone.utc).strftime("%d %b %Y")
    cierre = float(ultima[4])
    maximo = float(ultima[2])
    minimo = float(ultima[3])
    cambio = (cierre / float(anterior[4]) - 1) * 100

    est = analizar_estructura(preparar_velas(velas))
    fg_valor, fg_texto = fear_and_greed()

    flecha = "🟢" if cambio >= 0 else "🔴"
    mensaje = (
        f"<b>Como Estamos</b>\n"
        f"Vela del {fecha}\n\n"
        f"₿ Cierre: <b>${cierre:,.0f}</b> {flecha} {cambio:+.2f}%\n"
        f"Máximo: ${maximo:,.0f} | Mínimo: ${minimo:,.0f}\n\n"
        f"{texto_estructura(est)}\n\n"
        f"😨 Fear & Greed: <b>{fg_valor}</b> ({fg_texto})\n\n"
        f"<i>Fuente: {FUENTE}</i>"
    )

    respuesta = enviar_telegram(mensaje)
    print("Telegram:", "OK" if respuesta.get("ok") else respuesta)


if __name__ == "__main__":
    main()
