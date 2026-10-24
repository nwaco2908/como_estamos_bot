"""
Como Estamos - Módulo de estructura de mercado (Fase 3)

Reglas (vela diaria):
- Máximo de swing: vela cuyo máximo supera el de las N velas anteriores y las N siguientes.
- Mínimo de swing: igual, con el mínimo.
- BOS: cierre por encima del último máximo de swing (o por debajo del último mínimo)
  a favor de la tendencia actual.
- CHoCH: el mismo cierre, pero en contra de la tendencia actual (primer giro).
- Barrido: la vela perfora el último swing con la mecha pero cierra del otro lado.
"""

from datetime import datetime, timezone

N_SWING = 5  # igual que "Swing" en SMC Propio (5 velas a cada lado)


def _fecha(ts):
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%d %b %Y")


def preparar_velas(velas_crudas):
    """Convierte las velas de Kraken a diccionarios simples."""
    return [
        {
            "t": int(v[0]),
            "o": float(v[1]),
            "h": float(v[2]),
            "l": float(v[3]),
            "c": float(v[4]),
        }
        for v in velas_crudas
    ]


def analizar_estructura(velas, n=N_SWING):
    """
    Recorre las velas en orden y devuelve el estado de la estructura
    al cierre de la última vela.
    """
    tendencia = "rango"
    ultimo_max = None  # {"precio":..., "t":..., "roto": bool}
    ultimo_min = None
    eventos = []  # historial de BOS / CHoCH / barridos

    for i in range(len(velas)):
        # 1) Confirmar swings: la vela i-n ya tiene n velas a cada lado
        j = i - n
        if j >= n:
            centro = velas[j]
            izq = velas[j - n:j]
            der = velas[j + 1:j + n + 1]
            # Empates: se permite igualar a la izquierda, a la derecha debe ser estricto
            if all(centro["h"] >= v["h"] for v in izq) and all(centro["h"] > v["h"] for v in der):
                ultimo_max = {"precio": centro["h"], "t": centro["t"], "roto": False, "barrido": False}
            if all(centro["l"] <= v["l"] for v in izq) and all(centro["l"] < v["l"] for v in der):
                ultimo_min = {"precio": centro["l"], "t": centro["t"], "roto": False, "barrido": False}

        vela = velas[i]

        # 2) Rupturas por cierre
        if ultimo_max and not ultimo_max["roto"] and vela["c"] > ultimo_max["precio"]:
            tipo = "CHoCH alcista" if tendencia == "bajista" else "BOS alcista"
            eventos.append({"tipo": tipo, "t": vela["t"], "nivel": ultimo_max["precio"]})
            ultimo_max["roto"] = True
            tendencia = "alcista"
        elif ultimo_max and not ultimo_max["roto"] and not ultimo_max["barrido"] and vela["h"] > ultimo_max["precio"] and vela["c"] <= ultimo_max["precio"]:
            ultimo_max["barrido"] = True  # un barrido por nivel, como en SMC Propio
            eventos.append({"tipo": "Barrido de máximo", "t": vela["t"], "nivel": ultimo_max["precio"]})

        if ultimo_min and not ultimo_min["roto"] and vela["c"] < ultimo_min["precio"]:
            tipo = "CHoCH bajista" if tendencia == "alcista" else "BOS bajista"
            eventos.append({"tipo": tipo, "t": vela["t"], "nivel": ultimo_min["precio"]})
            ultimo_min["roto"] = True
            tendencia = "bajista"
        elif ultimo_min and not ultimo_min["roto"] and not ultimo_min["barrido"] and vela["l"] < ultimo_min["precio"] and vela["c"] >= ultimo_min["precio"]:
            ultimo_min["barrido"] = True
            eventos.append({"tipo": "Barrido de mínimo", "t": vela["t"], "nivel": ultimo_min["precio"]})

    ultima = velas[-1]
    eventos_hoy = [e for e in eventos if e["t"] == ultima["t"]]
    ultimo_evento = next(
        (e for e in reversed(eventos) if e["tipo"].startswith(("BOS", "CHoCH"))), None
    )

    return {
        "tendencia": tendencia,
        "ultimo_max": ultimo_max,
        "ultimo_min": ultimo_min,
        "eventos_hoy": eventos_hoy,
        "ultimo_evento": ultimo_evento,
        "eventos": eventos,
    }


def texto_estructura(est):
    """Bloque de texto para el mensaje de Telegram."""
    iconos = {"alcista": "📈 Alcista", "bajista": "📉 Bajista", "rango": "↔️ Rango"}
    lineas = [f"🧭 Estructura diaria: <b>{iconos[est['tendencia']]}</b>"]

    if est["ultimo_evento"]:
        e = est["ultimo_evento"]
        lineas.append(f"Último evento: {e['tipo']} en ${e['nivel']:,.0f} ({_fecha(e['t'])})")

    if est["ultimo_max"]:
        m = est["ultimo_max"]
        estado = " (roto)" if m["roto"] else ""
        lineas.append(f"Último máximo de swing: ${m['precio']:,.0f}{estado}")
    if est["ultimo_min"]:
        m = est["ultimo_min"]
        estado = " (roto)" if m["roto"] else ""
        lineas.append(f"Último mínimo de swing: ${m['precio']:,.0f}{estado}")

    if est["eventos_hoy"]:
        hoy = ", ".join(f"{e['tipo']} (${e['nivel']:,.0f})" for e in est["eventos_hoy"])
        lineas.append(f"⚡ Hoy: <b>{hoy}</b>")

    return "\n".join(lineas)
