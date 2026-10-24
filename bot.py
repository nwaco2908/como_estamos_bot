"""
Como Estamos - bot de sentimiento BTC - Fase 2 (prueba de conexión)
Envía a Telegram el cierre diario de BTC y el índice Fear & Greed.
"""

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "btc-sentiment-bot"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def velas_diarias_btc():
    """Velas diarias de BTC/USD desde Kraken: [tiempo, open, high, low, close, ...]"""
    data = get_json("https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval=1440")
    if data.get("error"):
        raise RuntimeError(f"Kraken error: {data['error']}")
    resultado = data["result"]
    clave = next(k for k in resultado if k != "last")
    velas = resultado[clave]
    # La última vela es el día en curso (sin cerrar); usamos solo velas cerradas
    return velas[:-1]


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

    fg_valor, fg_texto = fear_and_greed()

    flecha = "🟢" if cambio >= 0 else "🔴"
    mensaje = (
        f"<b>Como Estamos</b>\n"
        f"Vela del {fecha}\n\n"
        f"₿ Cierre: <b>${cierre:,.0f}</b> {flecha} {cambio:+.2f}%\n"
        f"Máximo: ${maximo:,.0f} | Mínimo: ${minimo:,.0f}\n\n"
        f"😨 Fear & Greed: <b>{fg_valor}</b> ({fg_texto})\n\n"
        f"<i>Fase 2: prueba de conexión. Estructura y noticias llegan en las próximas fases.</i>"
    )

    respuesta = enviar_telegram(mensaje)
    print("Telegram:", "OK" if respuesta.get("ok") else respuesta)


if __name__ == "__main__":
    main()
