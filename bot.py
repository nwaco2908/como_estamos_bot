"""
Como Estamos - bot de sentimiento BTC
Reporte diario: precio, escenario, régimen de fondo, estructura, sentimiento (noticias + Fear & Greed),
contexto macro, posicionamiento (longs vs shorts) y expectativas de la gente.
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
from escenarios import evaluar, fecha_iso, texto_escenario
import historial
from ciclo import linea_ciclo, lectura_combinada, obtener_ciclo, texto_ciclo
from macro import obtener_macro, texto_macro

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
# Uno o varios destinos separados por coma: tu chat personal, un canal (@nombre o -100...), un grupo
DESTINOS = [d.strip() for d in os.environ["TELEGRAM_CHAT_ID"].split(",") if d.strip()]


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


def _enviar_un_mensaje(texto, chat_id):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
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
    respuesta = {"ok": False}
    for destino in DESTINOS:
        try:
            for b in bloques:
                r = _enviar_un_mensaje(b[:4096], destino)
            print(f"Enviado a {destino[:4]}…: OK")
            respuesta = r
        except Exception as e:
            # Si un destino falla (p. ej. el bot no es admin del canal), los demás igual reciben
            print(f"No pude enviar a {destino[:4]}…: {e}")
    if not respuesta.get("ok"):
        raise RuntimeError("No se pudo enviar a ningún destino")
    return respuesta


def reporte_diario(velas, est, fg_hist, pos, sesgo_pos, hist):
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

    previos = [d for d in hist if d.get("fecha") != fecha_iso(ultima)]
    esc = evaluar(velas, est, fg_hist, combinado, noticias["puntaje"], sesgo_pos, sesgo_exp, previos)

    # Régimen de fondo (la marea) y contexto macro
    ult_noticias = [d.get("noticias") for d in previos[-6:] if d.get("noticias") is not None]
    if noticias["puntaje"] is not None:
        ult_noticias.append(noticias["puntaje"])
    noticias_prom = sum(ult_noticias) / len(ult_noticias) if ult_noticias else None
    ciclo = obtener_ciclo(velas, noticias_prom)
    try:
        mac = obtener_macro(dias=1)
    except Exception as e:
        print("Macro no disponible:", e)
        mac = None

    # Memoria: guardamos el día para los próximos análisis
    ls = (pos or {}).get("long_short") or {}
    fu = (pos or {}).get("funding") or {}
    try:
        historial.guardar({
            "fecha": fecha_iso(ultima),
            "cierre": round(cierre, 1), "maximo": round(maximo, 1), "minimo": round(minimo, 1),
            "tendencia": est["tendencia"],
            "eventos": [e["tipo"] for e in est["eventos_hoy"]],
            "fear_greed": fg_valor,
            "noticias": noticias["puntaje"],
            "n_titulares": noticias["total"],
            "narrativas": noticias["narrativas"],
            "sentimiento": combinado,
            "sesgo_multitud": sesgo_pos,
            "ratio_long_short": round(ls["ratio"], 3) if ls.get("ratio") else None,
            "funding": fu.get("actual"),
            "sesgo_expectativas": sesgo_exp,
            "escenario": f"{esc['nivel']} {esc['titulo']}",
            "regimen": ciclo["regimen"] if ciclo else None,
            "fase_regimen": f"{ciclo['icono']} {ciclo['fase']}" if ciclo else None,
            "macro": mac["puntaje"] if mac else None,
        })
    except Exception as e:
        print("No pude guardar el historial:", e)

    flecha = "🟢" if cambio >= 0 else "🔴"
    partes = [
        f"<b>Como Estamos</b>\nVela del {fecha}",
        f"₿ Cierre: <b>${cierre:,.0f}</b> {flecha} {cambio:+.2f}%\nMáximo: ${maximo:,.0f} | Mínimo: ${minimo:,.0f}",
        texto_escenario(esc),
    ]
    if ciclo:
        partes.append("\n".join(x for x in (linea_ciclo(ciclo), lectura_combinada(ciclo, est)) if x))
    partes += [texto_estructura(est), texto_noticias(noticias, combinado, fg_valor, fg_texto, fallidas)]
    if mac:
        partes.append(texto_macro(mac))
    partes += [texto_posicionamiento(pos, sesgo_pos), texto_expectativas(exp, sesgo_exp),
               f"<i>Fuente: {FUENTE}</i>"]
    return "\n\n".join(partes)


def reporte_semanal(velas, est, fg_hist, pos):
    titulares_sem, _ = obtener_titulares(horas=24 * 7)
    noticias_sem = analizar_noticias(titulares_sem, n=5)
    cierre = float(velas[-1][4])
    exp = obtener_expectativas(cierre, titulares_sem)
    desde = fecha_iso(velas[-7])
    escenarios_sem = [(d["fecha"], d.get("escenario")) for d in historial.cargar()
                      if d.get("fecha", "") >= desde and d.get("escenario")]
    hist = historial.cargar()
    ult = [d.get("noticias") for d in hist if d.get("fecha", "") >= desde and d.get("noticias") is not None]
    noticias_prom = sum(ult) / len(ult) if ult else noticias_sem["puntaje"]
    ciclo = obtener_ciclo(velas, noticias_prom)
    try:
        mac_txt = texto_macro(obtener_macro(dias=7, n=4), semanal=True)
    except Exception as e:
        print("Macro semanal no disponible:", e)
        mac_txt = ""
    return texto_semanal(velas, est, fg_hist[-7:], noticias_sem, pos,
                         texto_expectativas(exp, sesgo_expectativas(exp)), escenarios_sem,
                         texto_ciclo(ciclo), mac_txt)


def main():
    # MODO: "diario", "semanal" o "auto" (auto = diario, y además semanal si cerró el domingo)
    modo = (os.environ.get("MODO") or "auto").strip().lower()

    evento = (os.environ.get("EVENTO") or "").strip()

    velas = velas_diarias_btc()
    hist = historial.cargar()

    # Hay varias horas programadas por si GitHub se salta una. Si una corrida PROGRAMADA encuentra
    # que la vela de hoy ya fue reportada, no envía nada (evita mensajes repetidos).
    # Las corridas manuales siempre envían.
    if evento == "schedule" and modo == "auto":
        hoy = fecha_iso(velas[-1])
        if any(d.get("fecha") == hoy for d in hist):
            print(f"La vela del {hoy} ya fue reportada; esta corrida de respaldo no envía nada.")
            return

    est = analizar_estructura(preparar_velas(velas))
    fg_hist = fear_and_greed(10)
    pos = obtener_posicionamiento()
    sesgo_pos = sesgo_multitud(pos)

    domingo_cerrado = datetime.fromtimestamp(int(velas[-1][0]), tz=timezone.utc).weekday() == 6

    if modo in ("auto", "diario"):
        r = enviar_telegram(reporte_diario(velas, est, fg_hist, pos, sesgo_pos, hist))
        print("Diario:", "OK" if r.get("ok") else r)
    if modo == "semanal" or (modo == "auto" and domingo_cerrado):
        r = enviar_telegram(reporte_semanal(velas, est, fg_hist, pos))
        print("Semanal:", "OK" if r.get("ok") else r)


if __name__ == "__main__":
    main()
