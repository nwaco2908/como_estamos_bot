"""
Como Estamos - Módulo de expectativas (Fase 4c): ¿qué espera la gente?

Tres termómetros gratuitos de lo que el mercado ESPERA (no solo lo que opina):
1) Polymarket: probabilidades, con dinero real, de que BTC toque ciertos precios.
2) Deribit (opciones): ¿más apuestas a la baja (puts) o al alza (calls)? ¿en qué precios?
3) Titulares: precios objetivo que mencionan las noticias, arriba o abajo del precio actual.
"""

import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone


def _get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (como-estamos-bot)"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _monto(texto):
    """'$85,000' / '$85K' / '85k' / '$1.2M' -> número"""
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)\s?([kKmM])?", texto)
    if not m:
        return None
    try:
        num = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    suf = (m.group(2) or "").lower()
    if suf == "k":
        num *= 1_000
    elif suf == "m":
        num *= 1_000_000
    return num


# ───────────────────────── 1) POLYMARKET ─────────────────────────
SUBE = re.compile(r"\b(reach|hit|above|over|higher than|exceed|surpass)\b", re.IGNORECASE)
BAJA = re.compile(r"\b(dip|below|under|fall|drop|lower than|crash)\b", re.IGNORECASE)
NIVEL = re.compile(r"\$\s?\d[\d,\.]*\s?[kKmM]?")


def _mercados_polymarket():
    urls = [
        "https://gamma-api.polymarket.com/events?tag_slug=bitcoin&active=true&closed=false&limit=100",
        "https://gamma-api.polymarket.com/public-search?q=bitcoin%20price",
    ]
    mercados, ultimo_error = [], None
    for url in urls:
        try:
            data = _get_json(url)
            eventos = data if isinstance(data, list) else data.get("events", [])
            for ev in eventos:
                for m in ev.get("markets", []) or []:
                    m.setdefault("_evento", ev.get("title", ""))
                    mercados.append(m)
            if mercados:
                return mercados
        except Exception as e:
            ultimo_error = e
    if ultimo_error:
        raise RuntimeError(ultimo_error)
    return mercados


def polymarket(precio, dias_max=45):
    """
    Busca mercados tipo "¿Bitcoin tocará $X?" / "¿Bitcoin caerá a $X?" que venzan pronto
    y devuelve los niveles más cercanos arriba y abajo con su probabilidad.
    """
    ahora = datetime.now(timezone.utc)
    limite = ahora + timedelta(days=dias_max)
    arriba, abajo = [], []

    for m in _mercados_polymarket():
        if m.get("closed") or m.get("active") is False:
            continue
        pregunta = m.get("question") or ""
        if not re.search(r"\b(bitcoin|btc)\b", pregunta + " " + m.get("_evento", ""), re.IGNORECASE):
            continue
        nivel_txt = NIVEL.search(pregunta)
        if not nivel_txt:
            continue
        nivel = _monto(nivel_txt.group(0))
        if not nivel or not (precio * 0.5 < nivel < precio * 1.5):
            continue
        try:
            fin = datetime.fromisoformat((m.get("endDate") or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        if not (ahora < fin <= limite):
            continue
        try:
            resultados = json.loads(m.get("outcomes") or "[]")
            precios = [float(x) for x in json.loads(m.get("outcomePrices") or "[]")]
            prob_si = precios[[r.lower() for r in resultados].index("yes")]
        except Exception:
            continue
        # Mercados de "¿estará por encima de $X el día Y?" son distintos de "¿tocará $X?",
        # pero ambos sirven para ver hacia dónde se inclina la gente.
        fila = {"nivel": nivel, "prob": prob_si, "pregunta": pregunta, "fin": fin}
        if BAJA.search(pregunta) and nivel < precio:
            abajo.append(fila)
        elif SUBE.search(pregunta) and nivel > precio:
            arriba.append(fila)

    arriba.sort(key=lambda f: f["nivel"])
    abajo.sort(key=lambda f: -f["nivel"])
    return {"arriba": arriba[:2], "abajo": abajo[:2]}


# ───────────────────────── 2) DERIBIT (OPCIONES) ─────────────────────────
def opciones_deribit():
    data = _get_json(
        "https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency=BTC&kind=option"
    )
    puts = calls = 0.0
    oi_put, oi_call = {}, {}
    for o in data.get("result", []):
        partes = (o.get("instrument_name") or "").split("-")  # BTC-25OCT26-80000-P
        if len(partes) != 4:
            continue
        oi = float(o.get("open_interest") or 0)
        strike = float(partes[2])
        if partes[3] == "P":
            puts += oi
            oi_put[strike] = oi_put.get(strike, 0) + oi
        else:
            calls += oi
            oi_call[strike] = oi_call.get(strike, 0) + oi
    if not calls:
        return None
    put_mayor = max(oi_put.items(), key=lambda x: x[1]) if oi_put else None
    call_mayor = max(oi_call.items(), key=lambda x: x[1]) if oi_call else None
    return {"put_call": puts / calls, "puts": puts, "calls": calls,
            "put_mayor": put_mayor, "call_mayor": call_mayor}


# ───────────────────────── 3) OBJETIVOS EN TITULARES ─────────────────────────
OBJETIVO = re.compile(
    r"\b(to|toward|towards|target|targets|hit|hits|reach|reaches|at|below|above|under|over|eyes|tests?)\s+"
    r"(\$\s?\d[\d,\.]*\s?[kK]?|\d{2,3}\s?[kK])\b",
    re.IGNORECASE,
)


def objetivos_titulares(titulares, precio):
    arriba, abajo = [], []
    for t in titulares:
        for m in OBJETIVO.finditer(t["titulo"]):
            nivel = _monto(m.group(2))
            if not nivel or not (precio * 0.4 < nivel < precio * 2.5):
                continue
            if abs(nivel / precio - 1) < 0.01:  # el precio actual no es un objetivo
                continue
            (arriba if nivel > precio else abajo).append(nivel)
    return {"arriba": sorted(arriba), "abajo": sorted(abajo, reverse=True)}


# ───────────────────────── UNIR TODO ─────────────────────────
def obtener_expectativas(precio, titulares):
    res, fallos = {}, []
    for nombre, funcion in (("polymarket", lambda: polymarket(precio)), ("opciones", opciones_deribit)):
        try:
            res[nombre] = funcion()
        except Exception as e:
            print(f"Expectativas {nombre} no disponible:", e)
            res[nombre] = None
            fallos.append(nombre)
    res["titulares"] = objetivos_titulares(titulares, precio)
    res["fallos"] = fallos
    return res


def sesgo_expectativas(exp):
    """-100 = la gente espera bajada; +100 = espera subida."""
    partes = []
    pm = exp.get("polymarket")
    if pm and pm["arriba"] and pm["abajo"]:
        partes.append((pm["arriba"][0]["prob"] - pm["abajo"][0]["prob"]) * 100)
    op = exp.get("opciones")
    if op:
        # put/call ~0.7 es lo normal en BTC; 1.0 o más = mucha cobertura bajista
        partes.append(max(-100, min(100, (0.7 - op["put_call"]) / 0.3 * 100)))
    ti = exp.get("titulares") or {}
    n_ar, n_ab = len(ti.get("arriba", [])), len(ti.get("abajo", []))
    if n_ar + n_ab >= 2:
        partes.append((n_ar - n_ab) / (n_ar + n_ab) * 100)
    if not partes:
        return None
    return round(sum(partes) / len(partes))


def texto_expectativas(exp, sesgo):
    lineas = ["🔮 <b>¿Qué espera la gente?</b>"]
    pm = exp.get("polymarket")
    if pm and (pm["arriba"] or pm["abajo"]):
        for f in pm["arriba"][:1] + pm["abajo"][:1]:
            flecha = "⬆️" if f in pm["arriba"] else "⬇️"
            lineas.append(f"{flecha} Polymarket: {f['prob'] * 100:.0f}% a ${f['nivel']:,.0f} (vence {f['fin']:%d %b})")
    op = exp.get("opciones")
    if op:
        lineas.append(f"Opciones Deribit: put/call {op['put_call']:.2f} "
                      f"({'más apuestas a la baja' if op['put_call'] > 0.9 else 'más apuestas al alza' if op['put_call'] < 0.6 else 'equilibrado'})")
        if op["put_mayor"] and op["call_mayor"]:
            lineas.append(f"Más dinero en: puts ${op['put_mayor'][0]:,.0f} | calls ${op['call_mayor'][0]:,.0f}")
    ti = exp.get("titulares") or {}
    if ti.get("arriba") or ti.get("abajo"):
        txt = f"Objetivos en titulares: {len(ti['arriba'])} ⬆️ / {len(ti['abajo'])} ⬇️"
        if ti["abajo"]:
            txt += f" (más bajo: ${min(ti['abajo']):,.0f})"
        if ti["arriba"]:
            txt += f" (más alto: ${max(ti['arriba']):,.0f})"
        lineas.append(txt)
    if sesgo is not None:
        if sesgo <= -30:
            lectura = "La gente espera BAJADA ⬇️"
        elif sesgo >= 30:
            lectura = "La gente espera SUBIDA ⬆️"
        else:
            lectura = "Expectativas divididas ↔️"
        lineas.append(f"Expectativa: <b>{sesgo:+d}</b> {lectura}")
    if len(lineas) == 1:
        lineas.append("Sin datos de expectativas hoy")
    if exp.get("fallos"):
        lineas.append(f"<i>Sin datos: {', '.join(exp['fallos'])}</i>")
    return "\n".join(lineas)
