"""
Como Estamos - Módulo de noticias y sentimiento (Fase 4)

1) Descarga titulares de las últimas 24 h desde fuentes RSS gratuitas.
2) Se queda solo con los que hablan de Bitcoin / cripto.
3) Puntúa cada titular con un diccionario de palabras:
   - palabras negativas / positivas normales: peso 1
   - NARRATIVAS EXTREMAS (Ponzi, colapso, "bitcoin ha muerto", euforia...): peso 2
4) Combina noticias + Fear & Greed en una puntuación de -100 (pánico) a +100 (euforia).
"""

import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

FUENTES_RSS = {
    "Google News": "https://news.google.com/rss/search?q=bitcoin+when:{dias}d&hl=en-US&gl=US&ceid=US:en",
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph": "https://cointelegraph.com/rss",
    "Decrypt": "https://decrypt.co/feed",
    "Bitcoin Magazine": "https://bitcoinmagazine.com/.rss/full/",
}

# Solo titulares que hablen de esto
FILTRO_BTC = re.compile(
    r"\b(bitcoin|btc|crypto|cryptocurrenc|saylor|microstrategy|strategy inc|mstr|spot etf|bitcoin etf)",
    re.IGNORECASE,
)

# (patrón, peso). Los patrones se buscan como inicio de palabra, en minúsculas.
NEGATIVAS = {
    "crash": 1, "plunge": 1, "plummet": 1, "tumble": 1, "slump": 1, "sink": 1, "slide": 1,
    "drop": 1, "fall": 1, "falls": 1, "decline": 1, "sell-off": 1, "selloff": 1, "dump": 1,
    "bear": 1, "fear": 1, "liquidat": 1, "outflow": 1, "warn": 1, "risk": 1,
    "hack": 1, "exploit": 1, "ban": 1, "lawsuit": 1, "sues": 1, "probe": 1, "selling": 1,
    "loss": 1, "losses": 1, "weak": 1, "pressure": 1, "doubt": 1, "breakdown": 1,
    # narrativas extremas
    "ponzi": 2, "collapse": 2, "bankrupt": 2, "insolven": 2, "capitulat": 2, "panic": 2,
    "bloodbath": 2, "wipe": 2, "implode": 2, "fraud": 2, "death spiral": 2, "is dead": 2,
    "crypto winter": 2, "bubble burst": 2, "forced to sell": 2, "margin call": 2,
    "worst since": 2, "free fall": 2, "freefall": 2,
    "extreme fear": 2, "month low": 2, "year low": 2, "lowest since": 2, "lowest in": 2,
}
POSITIVAS = {
    "rally": 1, "surge": 1, "soar": 1, "jump": 1, "climb": 1, "gain": 1, "rise": 1,
    "rises": 1, "bull": 1, "inflow": 1, "rebound": 1, "recover": 1, "breakout": 1,
    "approve": 1, "approval": 1, "adopt": 1, "accumulat": 1, "buys": 1, "buying": 1,
    "optimis": 1, "strong": 1, "record": 1, "higher": 1, "upside": 1,
    # narrativas extremas
    "all-time high": 2, "all time high": 2, "new ath": 2, "euphoria": 2, "parabolic": 2,
    "to the moon": 2, "mania": 2, "fomo": 2, "supercycle": 2, "price discovery": 2,
    "can't stop": 2, "unstoppable": 2, "best week": 2, "best month": 2,
    "extreme greed": 2, "record high": 2,
}


def _patrones(diccionario):
    return [(re.compile(r"\b" + re.escape(p), re.IGNORECASE), p, w) for p, w in diccionario.items()]


PAT_NEG = _patrones(NEGATIVAS)
PAT_POS = _patrones(POSITIVAS)


def _descargar(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (como-estamos-bot)"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read()


def _fecha(texto):
    try:
        f = parsedate_to_datetime(texto)
        return f if f.tzinfo else f.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def obtener_titulares(horas=24):
    """Devuelve [{'titulo', 'fuente', 'fecha'}] de las últimas `horas`, sin duplicados."""
    limite = datetime.now(timezone.utc) - timedelta(hours=horas)
    titulares, vistos, fallidas = [], set(), []

    dias = max(1, round(horas / 24))
    for nombre, url in FUENTES_RSS.items():
        try:
            raiz = ET.fromstring(_descargar(url.replace("{dias}", str(dias))))
        except Exception as e:
            fallidas.append(nombre)
            print(f"RSS {nombre} no disponible: {e}")
            continue
        for item in raiz.iter("item"):
            titulo = (item.findtext("title") or "").strip()
            fecha = _fecha(item.findtext("pubDate") or "")
            if not titulo or (fecha and fecha < limite):
                continue
            if not FILTRO_BTC.search(titulo):
                continue
            # Algunos agregadores ponen " - Medio" al final; lo quitamos para detectar duplicados
            limpio = re.sub(r"\s+-\s+[^-]{2,40}$", "", titulo)
            clave = re.sub(r"\W+", " ", limpio.lower()).strip()
            if clave in vistos:
                continue
            vistos.add(clave)
            titulares.append({"titulo": limpio, "fuente": nombre, "fecha": fecha})

    return titulares, fallidas


def puntuar_titular(titulo):
    """Devuelve (puntaje, palabras_extremas). Negativo = bajista, positivo = alcista."""
    neg, pos, extremas = 0, 0, []
    for pat, palabra, peso in PAT_NEG:
        if pat.search(titulo):
            neg += peso
            if peso == 2:
                extremas.append(palabra)
    for pat, palabra, peso in PAT_POS:
        if pat.search(titulo):
            pos += peso
            if peso == 2:
                extremas.append(palabra)
    return pos - neg, extremas


def analizar_noticias(titulares, n=3):
    """
    Puntuación de noticias de -100 a +100 y narrativas extremas detectadas.
    """
    if not titulares:
        return {"puntaje": None, "total": 0, "neg": 0, "pos": 0, "neutras": 0,
                "narrativas": {}, "peores": [], "mejores": []}

    suma_neg, suma_pos = 0, 0
    neg = pos = neutras = 0
    narrativas = {}
    puntuados = []
    for t in titulares:
        p, extremas = puntuar_titular(t["titulo"])
        puntuados.append((p, t))
        if p < 0:
            neg += 1
            suma_neg += -p
        elif p > 0:
            pos += 1
            suma_pos += p
        else:
            neutras += 1
        for e in extremas:
            narrativas[e] = narrativas.get(e, 0) + 1

    # Balance entre lo positivo y lo negativo, suavizado si hay pocos titulares con tono
    con_tono = suma_neg + suma_pos
    puntaje = 0 if con_tono == 0 else (suma_pos - suma_neg) / (con_tono + 3) * 100

    puntuados.sort(key=lambda x: x[0])
    peores = [t for p, t in puntuados if p < 0][:n]
    mejores = [t for p, t in reversed(puntuados) if p > 0][:n]

    return {"puntaje": round(puntaje), "total": len(titulares), "neg": neg, "pos": pos,
            "neutras": neutras, "narrativas": narrativas, "peores": peores, "mejores": mejores}


def sentimiento_combinado(fg_valor, puntaje_noticias):
    """
    Fear & Greed (0-100) llevado a -100..+100, promediado con las noticias.
    Si no hay noticias, se usa solo el Fear & Greed.
    """
    fg = fg_valor * 2 - 100
    if puntaje_noticias is None:
        return round(fg)
    return round(0.5 * fg + 0.5 * puntaje_noticias)


def etiqueta_sentimiento(valor):
    if valor <= -60:
        return "😱 Pánico extremo"
    if valor <= -25:
        return "😨 Miedo"
    if valor < 25:
        return "😐 Neutral"
    if valor < 60:
        return "🙂 Optimismo"
    return "🤑 Euforia extrema"


def _escapar(texto):
    return texto.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def texto_noticias(analisis, combinado, fg_valor, fg_texto, fallidas):
    lineas = [f"🧠 Sentimiento: <b>{combinado:+d}</b> {etiqueta_sentimiento(combinado)}"]
    lineas.append(f"Fear &amp; Greed: {fg_valor} ({fg_texto})")

    if analisis["total"] == 0:
        lineas.append("📰 Noticias: sin titulares disponibles hoy")
    else:
        lineas.append(
            f"📰 Noticias 24 h: {analisis['puntaje']:+d} "
            f"({analisis['neg']} 🔴 / {analisis['pos']} 🟢 / {analisis['neutras']} ⚪ de {analisis['total']})"
        )
        if analisis["narrativas"]:
            top = sorted(analisis["narrativas"].items(), key=lambda x: -x[1])[:4]
            lineas.append("⚠️ Narrativas extremas: " + ", ".join(f"{k} ×{v}" for k, v in top))
        if analisis["peores"]:
            lineas.append("\nTitulares más negativos:")
            lineas += [f"🔴 {_escapar(t['titulo'])}" for t in analisis["peores"]]
        if analisis["mejores"]:
            lineas.append("\nTitulares más positivos:")
            lineas += [f"🟢 {_escapar(t['titulo'])}" for t in analisis["mejores"]]

    if fallidas:
        lineas.append(f"\n<i>Sin respuesta: {', '.join(fallidas)}</i>")
    return "\n".join(lineas)
