"""
Como Estamos - Contexto macro: ¿cómo está el mercado en general?

BTC no se mueve aislado: una guerra, el petróleo disparado o una Fed más dura pueden pesar
más que cualquier noticia cripto. Este módulo se mantiene SEPARADO del sentimiento cripto,
para saber si el miedo viene del mundo cripto o del mundo en general.

- Precios de referencia (Yahoo Finance, gratis): S&P 500, Nasdaq, petróleo (WTI), dólar (DXY), VIX.
- Titulares macro (Google News): bolsa, Fed, inflación, petróleo, guerra, aranceles.
- Puntaje de -100 (risk-off: miedo global) a +100 (risk-on: apetito por riesgo).
"""

import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

ACTIVOS = {
    "S&P 500": "^GSPC",
    "Nasdaq": "^IXIC",
    "Petróleo": "CL=F",
    "Dólar": "DX-Y.NYB",
    "VIX": "^VIX",
}

BUSQUEDAS = [
    "stock market OR Wall Street OR Nasdaq OR S&P 500",
    "Federal Reserve OR inflation OR interest rates OR Treasury yields",
    "oil prices OR Iran OR Middle East OR war OR tariffs OR sanctions",
]

RISK_OFF = {
    "war": 2, "strike": 1, "attack": 2, "missile": 2, "escalat": 2, "invasion": 2, "conflict": 1,
    "sanction": 1, "tariff": 1, "trade war": 2, "recession": 2, "slowdown": 1, "layoff": 1,
    "rate hike": 2, "hawkish": 1, "higher for longer": 2, "hotter": 1, "inflation rises": 1,
    "sell-off": 1, "selloff": 1, "plunge": 1, "tumble": 1, "slump": 1, "crash": 2, "fear": 1,
    "oil surges": 2, "oil jumps": 2, "oil spikes": 2, "shutdown": 1, "default": 2, "crisis": 2,
    "yields jump": 1, "yields surge": 1, "dollar surges": 1, "downgrade": 1, "panic": 2,
}
RISK_ON = {
    "ceasefire": 2, "truce": 2, "peace": 1, "deal": 1, "agreement": 1, "rate cut": 2, "dovish": 1,
    "cooling inflation": 2, "inflation eases": 2, "inflation cools": 2, "soft landing": 2,
    "rally": 1, "record high": 2, "all-time high": 2, "surge": 1, "gains": 1, "rebound": 1,
    "stimulus": 1, "strong jobs": 1, "beats": 1, "oil falls": 1, "oil drops": 1, "de-escalat": 2,
}
EXCLUIR = re.compile(r"\b(bitcoin|btc|crypto|ethereum)\b", re.IGNORECASE)  # eso ya lo cubre noticias.py


def _pat(d):
    return [(re.compile(r"\b" + re.escape(k), re.IGNORECASE), w) for k, w in d.items()]


P_OFF, P_ON = _pat(RISK_OFF), _pat(RISK_ON)


def _descargar(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (como-estamos-bot)"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read()


# ───────────────────────── PRECIOS ─────────────────────────
def _serie_yahoo(simbolo, rango="1mo"):
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(simbolo)}"
           f"?range={rango}&interval=1d")
    d = json.loads(_descargar(url).decode("utf-8"))
    r = d["chart"]["result"][0]
    cierres = [c for c in r["indicators"]["quote"][0]["close"] if c is not None]
    if len(cierres) < 2:
        raise RuntimeError("pocos datos")
    return cierres


def precios_macro():
    """{nombre: {"ultimo", "dia" (%), "semana" (%)}} con los activos que respondan."""
    res, fallos = {}, []
    for nombre, simbolo in ACTIVOS.items():
        try:
            c = _serie_yahoo(simbolo)
            res[nombre] = {
                "ultimo": c[-1],
                "dia": (c[-1] / c[-2] - 1) * 100,
                "semana": (c[-1] / c[-6] - 1) * 100 if len(c) >= 6 else None,
            }
        except Exception as e:
            print(f"Macro {nombre} no disponible:", e)
            fallos.append(nombre)
    return res, fallos


# ───────────────────────── TITULARES ─────────────────────────
def titulares_macro(dias=1):
    limite = datetime.now(timezone.utc) - timedelta(days=dias)
    vistos, salida = set(), []
    for q in BUSQUEDAS:
        url = ("https://news.google.com/rss/search?q=" + urllib.parse.quote(f"{q} when:{dias}d")
               + "&hl=en-US&gl=US&ceid=US:en")
        try:
            raiz = ET.fromstring(_descargar(url))
        except Exception as e:
            print("Titulares macro no disponibles:", e)
            continue
        for item in raiz.iter("item"):
            titulo = (item.findtext("title") or "").strip()
            try:
                fecha = parsedate_to_datetime(item.findtext("pubDate") or "")
            except Exception:
                fecha = None
            if not titulo or EXCLUIR.search(titulo) or (fecha and fecha < limite):
                continue
            limpio = re.sub(r"\s+-\s+[^-]{2,40}$", "", titulo)
            clave = re.sub(r"\W+", " ", limpio.lower()).strip()
            if clave in vistos:
                continue
            vistos.add(clave)
            salida.append(limpio)
    return salida


def _puntuar(titulo):
    return sum(w for p, w in P_ON if p.search(titulo)) - sum(w for p, w in P_OFF if p.search(titulo))


def analizar_macro(precios, titulares, n=3):
    # Componente de titulares
    puntuados = sorted(((_puntuar(t), t) for t in titulares), key=lambda x: x[0])
    neg = sum(-p for p, _ in puntuados if p < 0)
    pos = sum(p for p, _ in puntuados if p > 0)
    p_tit = (pos - neg) / (pos + neg + 3) * 100 if (pos + neg) else None

    # Componente de mercados (movimiento del último día)
    p_mer = None
    if precios:
        partes = []
        for nombre, peso in (("Nasdaq", 25), ("S&P 500", 25)):
            if nombre in precios:
                partes.append(precios[nombre]["dia"] * peso)       # bolsa sube = risk-on
        if "VIX" in precios:
            partes.append(-precios["VIX"]["dia"] * 3)              # VIX sube = miedo
        if "Dólar" in precios:
            partes.append(-precios["Dólar"]["dia"] * 40)           # dólar fuerte = malo para BTC
        if "Petróleo" in precios and precios["Petróleo"]["dia"] > 2:
            partes.append(-(precios["Petróleo"]["dia"] - 2) * 10)  # petróleo disparado = tensión
        if partes:
            p_mer = max(-100, min(100, sum(partes) / len(partes) * 2))

    comps = [x for x in (p_tit, p_mer) if x is not None]
    puntaje = round(sum(comps) / len(comps)) if comps else None
    return {
        "puntaje": puntaje, "titulares_puntaje": p_tit, "mercados_puntaje": p_mer,
        "negativos": [t for p, t in puntuados if p < 0][:n],
        "positivos": [t for p, t in reversed(puntuados) if p > 0][:n],
        "total": len(titulares),
    }


def obtener_macro(dias=1, n=3):
    precios, fallos = precios_macro()
    titulares = titulares_macro(dias)
    a = analizar_macro(precios, titulares, n)
    a["precios"], a["fallos"] = precios, fallos
    return a


def _esc(t):
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def etiqueta_macro(p):
    if p is None:
        return ""
    if p <= -40:
        return "🔴 Risk-off fuerte (miedo global)"
    if p <= -15:
        return "🟠 Risk-off (cautela)"
    if p < 15:
        return "⚪ Neutral"
    if p < 40:
        return "🟢 Risk-on (apetito por riesgo)"
    return "🟢 Risk-on fuerte"


def texto_macro(m, semanal=False):
    if not m:
        return ""
    lineas = ["🌍 <b>Contexto macro</b>"
              + (f": <b>{m['puntaje']:+d}</b> {etiqueta_macro(m['puntaje'])}" if m["puntaje"] is not None else "")]
    if m["precios"]:
        clave = "semana" if semanal else "dia"
        partes = []
        for nombre in ACTIVOS:
            if nombre in m["precios"] and m["precios"][nombre][clave] is not None:
                v = m["precios"][nombre]
                if nombre == "VIX":
                    partes.append(f"VIX {v['ultimo']:.1f} ({v[clave]:+.1f}%)")
                else:
                    partes.append(f"{_esc(nombre)} {v[clave]:+.1f}%")
        periodo = "Semana" if semanal else "Último cierre"
        lineas.append(f"{periodo}: " + " | ".join(partes))
    if m["negativos"]:
        lineas += [f"🔴 {_esc(t)}" for t in m["negativos"]]
    if m["positivos"]:
        lineas += [f"🟢 {_esc(t)}" for t in m["positivos"]]
    if m.get("fallos"):
        lineas.append(f"<i>Sin datos: {', '.join(m['fallos'])}</i>")
    return "\n".join(lineas)
