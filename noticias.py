"""
Como Estamos - Módulo de noticias y sentimiento (Fase 4)

1) Descarga titulares de las últimas 24 h desde fuentes RSS gratuitas.
   Si Google News no responde, usa fuentes de respaldo para no quedarse sin noticias.
2) Se queda solo con los que hablan de Bitcoin / cripto y quita la misma noticia repetida
   (aunque venga redactada distinto por otro medio).
3) Puntúa cada titular con un diccionario de palabras Y reglas de contexto:
   - palabras negativas / positivas normales: peso 1
   - NARRATIVAS EXTREMAS (Ponzi, colapso, "bitcoin ha muerto", euforia...): peso 2
   - "borra el rally" es negativo aunque diga "rally"; "recupera las pérdidas" es positivo
   - un impuesto a las "ganancias" no es una buena noticia
   - "desde su máximo histórico" o una pregunta ("¿llegará a máximos?") no son euforia
   - "aguanta $75K pese al miedo" es RESILIENCIA: no cuenta como negativo y se reporta aparte
4) Combina noticias + Fear & Greed en una puntuación de -100 (pánico) a +100 (euforia).
   Si hay muy pocos titulares, las noticias NO cuentan ese día (se avisa en el reporte).
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
# Solo se consultan si Google News (la fuente con más titulares) no responde
RESPALDO_RSS = {
    "Bing News": "https://www.bing.com/news/search?q=bitcoin&format=rss",
    "CryptoSlate": "https://cryptoslate.com/feed/",
    "Bitcoin.com": "https://news.bitcoin.com/feed/",
}
MIN_TITULARES = 8  # con menos titulares que esto, el puntaje de noticias no es fiable

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
    "crater": 1, "rout": 1, "downside": 1, "stolen": 1, "breach": 1,
    # narrativas extremas
    "ponzi": 2, "collapse": 2, "bankrupt": 2, "insolven": 2, "capitulat": 2, "panic": 2,
    "bloodbath": 2, "wipe": 2, "implode": 2, "fraud": 2, "death spiral": 2, "is dead": 2,
    "crypto winter": 2, "bubble burst": 2, "forced to sell": 2, "margin call": 2,
    "worst since": 2, "free fall": 2, "freefall": 2,
    "extreme fear": 2, "month low": 2, "year low": 2, "lowest since": 2, "lowest in": 2,
}
POSITIVAS = {
    "rally": 1, "rallies": 1, "surge": 1, "soar": 1, "jump": 1, "climb": 1, "gain": 1, "rise": 1,
    "rises": 1, "bull": 1, "inflow": 1, "rebound": 1, "recover": 1, "breakout": 1,
    "approve": 1, "approval": 1, "adopt": 1, "accumulat": 1, "buys": 1, "buying": 1,
    "optimis": 1, "strong": 1, "record": 1, "higher": 1, "upside": 1,
    # narrativas extremas
    "all-time high": 2, "all time high": 2, "new ath": 2, "euphoria": 2, "parabolic": 2,
    "to the moon": 2, "mania": 2, "fomo": 2, "supercycle": 2, "price discovery": 2,
    "can't stop": 2, "unstoppable": 2, "best week": 2, "best month": 2,
    "extreme greed": 2, "record high": 2,
}

# ── Reglas de contexto ──
_HASTA_3 = r"(?:\W+[\w$%.,'-]+){0,3}?\W+"
# "Erases election rally", "gives up gains": lo positivo quedó anulado → negativo
ANULA_POS = re.compile(
    r"\b(erases?|erased|wipes? out|wiped out|gives? up|gave up|loses?|lost|ends?|ended|fades?|faded|fading|"
    r"stalls?|stalled|fails?|failed|reverses?|reversed|snaps?|snapped|pares?|trims?|unwinds?|fizzles?)"
    + _HASTA_3 + r"(rally|rallies|gains?|rebound|recovery|surge|advance|bounce)\b", re.IGNORECASE)
# "Erases losses", "recovers from sell-off": lo negativo quedó anulado → positivo
ANULA_NEG = re.compile(
    r"\b(erases?|erased|recovers?|recovered|recoups?|recouped|pares?|pared|trims?|trimmed|reverses?|reversed)"
    + _HASTA_3 + r"(loss(?:es)?|declines?|drops?|slide|sell-?off|slump)\b", re.IGNORECASE)
# El precio aguanta pese a las malas noticias
RESILIENCIA = re.compile(
    r"\b(holds?|holding|steadies|steady|stabili[sz]es?|resilien\w*|shrugs? off|defies|defying|unfazed|"
    r"ignores?|weathers?|withstands?|stays? above|remains? above|refuses to)\b", re.IGNORECASE)
# "Needs a close above X to spark rebound", "without betting on a rally may not pay": condicionan, no afirman
CONDICIONAL = re.compile(
    r"\b(needs?|must|has to|have to)\b[^;:]*\bto\b|\bmay not\b|\bmight not\b|\bwithout\b|\bunless\b|\bif\b",
    re.IGNORECASE)
IMPUESTO = re.compile(r"\b(tax|taxes|taxation|levy|levies)\b", re.IGNORECASE)
# Preguntas y predicciones: especulan, no describen lo que pasa
ESPECULATIVO = re.compile(r"\?\s*$|^\W*(prediction|opinion|could|will|can|what if|is it)\b", re.IGNORECASE)
# "Since its $126,000 record high": habla del pasado, no es euforia
RETROSPECTIVO = re.compile(
    r"\b(since|from|below|off|under|shy of|after|away from)\b[^;:]{0,40}\b(record|all[- ]time high|ath)\b",
    re.IGNORECASE)
# "Record outflows", "record liquidations": récord de algo malo
RECORD_NEGATIVO = re.compile(
    r"\brecord\W+(?:\w+\W+)?(outflows?|liquidations?|loss(?:es)?|lows?|sell|selling|drop|decline)", re.IGNORECASE)
_PALABRAS_RECORD = {"record", "record high", "all-time high", "all time high", "new ath"}
_PALABRAS_GANANCIA = {"gain", "approve", "approval"}

# Palabras que no ayudan a distinguir una noticia de otra
_GENERICAS = {
    "bitcoin", "btc", "crypto", "cryptocurrency", "cryptocurrencies", "price", "prices", "market",
    "markets", "today", "the", "and", "for", "with", "from", "that", "this", "after", "amid", "over",
    "into", "its", "are", "was", "has", "have", "will", "what", "why", "how", "new", "says", "say",
}


def _patrones(diccionario):
    return [(re.compile(r"\b" + re.escape(p), re.IGNORECASE), p, w) for p, w in diccionario.items()]


PAT_NEG = _patrones(NEGATIVAS)
PAT_POS = _patrones(POSITIVAS)


def _descargar(url, intentos=1):
    ultimo = None
    for _ in range(intentos):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (como-estamos-bot)"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.read()
        except Exception as e:
            ultimo = e
    raise ultimo


def _fecha(texto):
    try:
        f = parsedate_to_datetime(texto)
        return f if f.tzinfo else f.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _palabras_clave(titulo):
    """Palabras (y números) que identifican la noticia, sin las genéricas."""
    return {w for w in re.findall(r"[a-z]{3,}|\d+", titulo.lower()) if w not in _GENERICAS}


def _misma_noticia(a, b):
    """La misma noticia redactada distinto: comparten la mayoría de sus palabras clave."""
    menor = min(len(a), len(b))
    if menor < 4:
        return a == b
    return len(a & b) / menor >= 0.6


def obtener_titulares(horas=24):
    """Devuelve ([{'titulo', 'fuente', 'fecha'}], fuentes_sin_respuesta) de las últimas `horas`,
    sin noticias repetidas."""
    limite = datetime.now(timezone.utc) - timedelta(hours=horas)
    titulares, claves, fallidas = [], [], []
    dias = max(1, round(horas / 24))

    def leer(nombre, url, intentos=1):
        try:
            raiz = ET.fromstring(_descargar(url.replace("{dias}", str(dias)), intentos))
        except Exception as e:
            print(f"RSS {nombre} no disponible: {e}")
            return False
        for item in raiz.iter("item"):
            titulo = (item.findtext("title") or "").strip()
            fecha = _fecha(item.findtext("pubDate") or "")
            if not titulo or (fecha and fecha < limite):
                continue
            if not FILTRO_BTC.search(titulo):
                continue
            # Algunos agregadores ponen " - Medio" al final; lo quitamos para detectar duplicados
            limpio = re.sub(r"\s+-\s+[^-]{2,40}$", "", titulo)
            clave = _palabras_clave(limpio)
            if any(_misma_noticia(clave, c) for c in claves):
                continue
            claves.append(clave)
            titulares.append({"titulo": limpio, "fuente": nombre, "fecha": fecha})
        return True

    for nombre, url in FUENTES_RSS.items():
        if not leer(nombre, url, intentos=2 if nombre == "Google News" else 1):
            fallidas.append(nombre)

    if "Google News" in fallidas:
        usados = [nombre for nombre, url in RESPALDO_RSS.items() if leer(nombre, url)]
        i = fallidas.index("Google News")
        fallidas[i] = ("Google News (usé de respaldo: " + ", ".join(usados) + ")") if usados \
            else "Google News (y los respaldos tampoco)"

    return titulares, fallidas


def _puntuar(titulo):
    """Puntúa un titular con el diccionario y las reglas de contexto."""
    t = titulo
    neg = pos = 0
    extremas = []
    especulativo = bool(ESPECULATIVO.search(t))

    m = ANULA_POS.search(t)
    if m:  # "erases rally": negativo, y ese "rally" ya no cuenta como positivo
        neg += 1
        t = t.replace(m.group(0), " ")
    m = ANULA_NEG.search(t)
    if m:  # "erases losses": positivo, y esas "losses" ya no cuentan como negativo
        pos += 1
        t = t.replace(m.group(0), " ")

    condicional = bool(CONDICIONAL.search(t))
    impuesto = bool(IMPUESTO.search(t))
    retro = bool(RETROSPECTIVO.search(t))
    record_malo = bool(RECORD_NEGATIVO.search(t))

    for pat, palabra, peso in PAT_NEG:
        if pat.search(t):
            neg += peso
            if peso == 2:
                extremas.append(palabra)
    for pat, palabra, peso in PAT_POS:
        if not pat.search(t):
            continue
        if condicional:
            continue  # "necesita X para rebotar" no es una subida
        if impuesto and palabra in _PALABRAS_GANANCIA:
            continue  # "tax on crypto gains" no es una ganancia
        if palabra in _PALABRAS_RECORD and (retro or especulativo or record_malo):
            continue  # máximos del pasado, preguntas o récords de algo malo
        if peso == 2 and especulativo:
            continue  # una pregunta no es euforia
        pos += peso
        if peso == 2:
            extremas.append(palabra)

    resiliencia = bool(RESILIENCIA.search(t)) and neg > 0
    if resiliencia:
        neg = 0  # hay malas noticias pero el precio aguanta: no es un titular bajista
        extremas = [e for e in extremas if e in POSITIVAS]
    return {"puntaje": pos - neg, "extremas": extremas, "resiliencia": resiliencia}


def puntuar_titular(titulo):
    """Devuelve (puntaje, palabras_extremas). Negativo = bajista, positivo = alcista."""
    r = _puntuar(titulo)
    return r["puntaje"], r["extremas"]


def analizar_noticias(titulares, n=3):
    """
    Puntuación de noticias de -100 a +100 y narrativas extremas detectadas.
    "fiable" indica si hay suficientes titulares para confiar en el puntaje.
    """
    if not titulares:
        return {"puntaje": None, "total": 0, "neg": 0, "pos": 0, "neutras": 0, "fiable": False,
                "narrativas": {}, "peores": [], "mejores": [], "resiliencia": []}

    suma_neg, suma_pos = 0, 0
    neg = pos = neutras = 0
    narrativas = {}
    puntuados, resiliencia = [], []
    for t in titulares:
        r = _puntuar(t["titulo"])
        p = r["puntaje"]
        puntuados.append((p, t))
        if r["resiliencia"]:
            resiliencia.append(t)
        if p < 0:
            neg += 1
            suma_neg += -p
        elif p > 0:
            pos += 1
            suma_pos += p
        else:
            neutras += 1
        for e in r["extremas"]:
            narrativas[e] = narrativas.get(e, 0) + 1

    # Balance entre lo positivo y lo negativo, suavizado si hay pocos titulares con tono
    con_tono = suma_neg + suma_pos
    puntaje = 0 if con_tono == 0 else (suma_pos - suma_neg) / (con_tono + 3) * 100

    puntuados.sort(key=lambda x: x[0])
    peores = [t for p, t in puntuados if p < 0][:n]
    mejores = [t for p, t in reversed(puntuados) if p > 0][:n]

    return {"puntaje": round(puntaje), "total": len(titulares), "neg": neg, "pos": pos,
            "neutras": neutras, "fiable": len(titulares) >= MIN_TITULARES,
            "narrativas": narrativas, "peores": peores, "mejores": mejores, "resiliencia": resiliencia}


def puntaje_fiable(analisis):
    """El puntaje de noticias, o None si hay tan pocos titulares que no conviene usarlo."""
    return analisis["puntaje"] if analisis.get("fiable") else None


def sentimiento_combinado(fg_valor, puntaje_noticias):
    """
    Fear & Greed (0-100) llevado a -100..+100, promediado con las noticias.
    Si no hay noticias (o no son fiables), se usa solo el Fear & Greed.
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
        lineas.append("📰 Noticias: sin titulares disponibles hoy (el sentimiento usa solo Fear &amp; Greed)")
    else:
        lineas.append(
            f"📰 Noticias 24 h: {analisis['puntaje']:+d} "
            f"({analisis['neg']} 🔴 / {analisis['pos']} 🟢 / {analisis['neutras']} ⚪ de {analisis['total']})"
        )
        if not analisis["fiable"]:
            lineas.append(f"⚠️ Muy pocos titulares (menos de {MIN_TITULARES}): hoy las noticias NO cuentan "
                          "para el sentimiento, que usa solo Fear &amp; Greed.")
        if analisis["narrativas"]:
            top = sorted(analisis["narrativas"].items(), key=lambda x: -x[1])[:4]
            lineas.append("⚠️ Narrativas extremas: " + ", ".join(f"{k} ×{v}" for k, v in top))
        if analisis["resiliencia"]:
            lineas.append(f"💪 Resiliencia: {len(analisis['resiliencia'])} titular(es) dicen que el precio "
                          "aguanta pese a las malas noticias")
        if analisis["peores"]:
            lineas.append("\nTitulares más negativos:")
            lineas += [f"🔴 {_escapar(t['titulo'])}" for t in analisis["peores"]]
        if analisis["mejores"]:
            lineas.append("\nTitulares más positivos:")
            lineas += [f"🟢 {_escapar(t['titulo'])}" for t in analisis["mejores"]]

    if fallidas:
        lineas.append(f"\n<i>Sin respuesta: {', '.join(fallidas)}</i>")
    return "\n".join(lineas)
