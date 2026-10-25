"""
Como Estamos - Régimen de fondo + fase del retroceso (horizonte de semanas)

Las olas y la marea:
- La MAREA (régimen) = estructura principal, swing de 25 velas (como el HH/LL del SMC Propio).
- Las OLAS = la estructura interna de swing 5 (la del reporte diario).

El concepto: la psicología del mercado quiere hacernos ver precios más abajo (o más arriba)
y el precio le lleva la contraria dentro de la tendencia de fondo.

RÉGIMEN ALCISTA                                   RÉGIMEN BAJISTA (espejo)
🟢 Tendencia sana (cerca de máximos)             ⚪ Tendencia bajista (cerca de mínimos)
🟡 Retroceso con miedo                           🟡 Rebote con optimismo
🟠 Retroceso absorbido (miedo, ya no cae)        🟠 Rebote agotado (optimismo, ya no sube)
🔴 Reanudación alcista                           🔴 Reanudación bajista
⚠️ Euforia en máximos (posible techo)            🚨 Capitulación (posible cambio de régimen)
                                                 🟠 Rebote con miedo (posible cambio de régimen)
🚨 Cambio de régimen: CHoCH en la estructura principal.
"""

import json
import urllib.request
from datetime import datetime, timezone

from estructura import analizar_estructura, preparar_velas

SWING_PRINCIPAL = 25
RETROCESO_MIN = 3.0   # % desde el máximo/mínimo para hablar de retroceso o rebote
REANUDA_MIN = 5.0     # % de recuperación desde el extremo del retroceso para hablar de reanudación


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "como-estamos-bot"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fear_greed_90():
    d = _get("https://api.alternative.me/fng/?limit=90")
    return [int(x["value"]) for x in reversed(d["data"])]  # del más viejo al más nuevo


def funding_30d():
    d = _get("https://api.bitget.com/api/v2/mix/market/history-fund-rate"
             "?symbol=BTCUSDT&productType=usdt-futures&pageSize=90")
    vals = [float(h["fundingRate"]) for h in d.get("data") or [] if h.get("fundingRate") not in (None, "")]
    return sum(vals) / len(vals) if vals else None


def _prom(lista):
    lista = [x for x in lista if x is not None]
    return sum(lista) / len(lista) if lista else None


def _f(ts):
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%d %b")


def analizar_ciclo(velas, fg, funding_prom=None, noticias_prom=None):
    """
    velas: velas diarias cerradas [t, o, h, l, c] (idealmente 200+)
    fg: valores diarios de Fear & Greed, del más viejo al más nuevo
    noticias_prom: promedio del puntaje de noticias de los últimos días (de la memoria), si existe
    """
    cierres = [float(v[4]) for v in velas]
    maximos = [float(v[2]) for v in velas]
    minimos = [float(v[3]) for v in velas]
    p = cierres[-1]
    hoy = len(velas) - 1

    est_p = analizar_estructura(preparar_velas(velas), n=SWING_PRINCIPAL)
    regimen = est_p["tendencia"]

    # Inicio del régimen: última ruptura principal (BOS/CHoCH) a favor de la tendencia
    rupturas = [e for e in est_p["eventos"] if e["tipo"].startswith(("BOS", "CHoCH"))]
    inicio_t = rupturas[-1]["t"] if rupturas else int(velas[max(0, hoy - 60)][0])
    idx_inicio = next((i for i, v in enumerate(velas) if int(v[0]) >= inicio_t), max(0, hoy - 60))
    idx_inicio = max(idx_inicio - SWING_PRINCIPAL * 2, hoy - 120, 0)  # incluye el impulso que llevó a la ruptura
    cambio_reciente = bool(rupturas) and rupturas[-1]["tipo"].startswith("CHoCH") and \
        (int(velas[-1][0]) - rupturas[-1]["t"]) <= 10 * 86400

    fg7, fg14, fg30, fg90 = _prom(fg[-7:]), _prom(fg[-14:]), _prom(fg[-30:]), _prom(fg)
    miedo = (fg7 is not None and fg7 <= 45) or (noticias_prom is not None and noticias_prom <= -25)
    optimismo = (fg7 is not None and fg7 >= 55) or (noticias_prom is not None and noticias_prom >= 25)

    fase, icono, lectura = "Sin régimen claro", "⚪", "La estructura principal todavía no define tendencia."
    detalle = {}

    if regimen == "alcista":
        # Máximo del régimen y retroceso desde ahí
        tramo = range(idx_inicio, hoy + 1)
        i_max = max(tramo, key=lambda i: maximos[i])
        techo = maximos[i_max]
        retroceso = (1 - p / techo) * 100
        dias_desde_max = hoy - i_max
        piso_ret = min(minimos[i_max:]) if i_max < hoy else minimos[hoy]
        i_piso = i_max + min(range(hoy - i_max + 1), key=lambda k: minimos[i_max + k])
        recupera = (p / piso_ret - 1) * 100
        dias_desde_piso = hoy - i_piso
        detalle = {"extremo": techo, "extremo_fecha": velas[i_max][0], "movimiento": retroceso,
                   "contra_extremo": piso_ret, "recupera": recupera}

        if cambio_reciente:
            fase, icono = "Cambio a régimen alcista", "🚨"
            lectura = "CHoCH alcista en la estructura principal: la marea pudo haber cambiado a favor de los toros."
        elif dias_desde_max <= 5 and retroceso < RETROCESO_MIN and fg7 is not None and fg7 >= 70:
            fase, icono = "Euforia en máximos", "⚠️"
            lectura = "Codicia extrema con el precio en máximos: cuidado, aquí se forman los techos."
        elif retroceso < RETROCESO_MIN:
            fase, icono = "Tendencia alcista sana", "🟢"
            lectura = "Régimen alcista y el precio cerca de sus máximos."
        elif dias_desde_piso >= 3 and recupera >= REANUDA_MIN and fg7 is not None and fg7 <= 55:
            fase, icono = "Reanudación alcista", "🔴"
            lectura = (f"El precio rebotó {recupera:.0f}% desde el piso del retroceso mientras el sentimiento "
                       "sigue sin euforia: la tendencia de fondo retoma el control.")
        elif dias_desde_piso >= 3 and miedo:
            fase, icono = "Retroceso absorbido", "🟠"
            lectura = (f"Retroceso de {retroceso:.0f}% con miedo, pero hace {dias_desde_piso} días que el precio "
                       "no hace nuevos mínimos: las malas noticias ya no lo empujan abajo.")
        elif miedo:
            fase, icono = "Retroceso con miedo", "🟡"
            lectura = (f"El precio retrocede {retroceso:.0f}% desde el máximo y el sentimiento se volvió negativo, "
                       "con la estructura principal intacta. Vigilar si deja de caer.")
        else:
            fase, icono = "Retroceso sin miedo", "⚪"
            lectura = f"Retroceso de {retroceso:.0f}% sin pánico: corrección normal, sin ventaja contrarian clara."

    elif regimen == "bajista":
        tramo = range(idx_inicio, hoy + 1)
        i_min = min(tramo, key=lambda i: minimos[i])
        piso = minimos[i_min]
        rebote = (p / piso - 1) * 100
        dias_desde_min = hoy - i_min
        techo_reb = max(maximos[i_min:]) if i_min < hoy else maximos[hoy]
        i_techo = i_min + max(range(hoy - i_min + 1), key=lambda k: maximos[i_min + k])
        cae = (1 - p / techo_reb) * 100
        dias_desde_techo = hoy - i_techo
        detalle = {"extremo": piso, "extremo_fecha": velas[i_min][0], "movimiento": rebote,
                   "contra_extremo": techo_reb, "recupera": cae}

        if cambio_reciente:
            fase, icono = "Cambio a régimen bajista", "🚨"
            lectura = "CHoCH bajista en la estructura principal: la marea pudo haber cambiado a favor de los osos."
        elif dias_desde_min <= 5 and rebote < 8 and fg7 is not None and fg7 <= 25:
            fase, icono = "Capitulación", "🚨"
            lectura = ("Mínimos con pánico extremo. Así empezó el suelo de julio 2026: vigilar si el precio "
                       "deja de caer mientras el miedo sigue (posible cambio de régimen).")
        elif rebote >= 7 and fg14 is not None and fg14 <= 40:
            fase, icono = "Rebote con miedo", "🟠"
            lectura = (f"El precio subió {rebote:.0f}% desde el mínimo y la gente sigue con miedo. "
                       "Divergencia como la de julio 2026: posible cambio de régimen si rompe la estructura principal.")
        elif rebote < RETROCESO_MIN:
            fase, icono = "Tendencia bajista", "⚪"
            lectura = "Régimen bajista y el precio cerca de sus mínimos."
        elif dias_desde_techo >= 3 and cae >= REANUDA_MIN and fg7 is not None and fg7 >= 45:
            fase, icono = "Reanudación bajista", "🔴"
            lectura = (f"El precio cayó {cae:.0f}% desde el techo del rebote mientras la gente seguía optimista: "
                       "la tendencia de fondo retoma el control.")
        elif dias_desde_techo >= 3 and optimismo:
            fase, icono = "Rebote agotado", "🟠"
            lectura = (f"Rebote de {rebote:.0f}% con optimismo, pero hace {dias_desde_techo} días que no hace "
                       "nuevos máximos: las buenas noticias ya no lo empujan arriba.")
        elif optimismo:
            fase, icono = "Rebote con optimismo", "🟡"
            lectura = (f"El precio rebota {rebote:.0f}% desde el mínimo con sentimiento positivo, en régimen bajista. "
                       "Vigilar si deja de subir.")
        else:
            fase, icono = "Rebote sin optimismo", "⚪"
            lectura = f"Rebote de {rebote:.0f}% sin euforia: movimiento normal dentro de la tendencia bajista."

    # Niveles de la estructura principal
    nivel_choch = None
    if regimen == "alcista" and est_p.get("ultimo_min") and not est_p["ultimo_min"]["roto"]:
        nivel_choch = est_p["ultimo_min"]["precio"]
    elif regimen == "bajista" and est_p.get("ultimo_max") and not est_p["ultimo_max"]["roto"]:
        nivel_choch = est_p["ultimo_max"]["precio"]

    return {
        "regimen": regimen, "fase": fase, "icono": icono, "lectura": lectura, "detalle": detalle,
        "precio": p, "nivel_choch": nivel_choch, "est_principal": est_p,
        "fg7": fg7, "fg30": fg30, "fg90": fg90,
        "ext_miedo_30": sum(1 for x in fg[-30:] if x <= 25),
        "ext_euforia_30": sum(1 for x in fg[-30:] if x >= 75),
        "funding_30d": funding_prom, "noticias_prom": noticias_prom,
        "ret30": (p / cierres[-31] - 1) * 100 if len(cierres) > 30 else None,
    }


def obtener_ciclo(velas, noticias_prom=None):
    try:
        fg = fear_greed_90()
    except Exception as e:
        print("Fear & Greed de 90 días no disponible:", e)
        return None
    fu = None
    try:
        fu = funding_30d()
    except Exception as e:
        print("Funding de 30 días no disponible:", e)
    try:
        return analizar_ciclo(velas, fg, fu, noticias_prom)
    except Exception as e:
        print("No pude analizar el régimen:", e)
        return None


def lectura_combinada(c, est_interna):
    """Cruza la ola (estructura interna) con la marea (régimen)."""
    if not c or c["regimen"] not in ("alcista", "bajista"):
        return ""
    interna = est_interna["tendencia"]
    nivel = f" mientras no cierre bajo ${c['nivel_choch']:,.0f}" if c["regimen"] == "alcista" and c["nivel_choch"] else \
            f" mientras no cierre sobre ${c['nivel_choch']:,.0f}" if c["nivel_choch"] else ""
    if c["regimen"] == "alcista" and interna == "bajista":
        return f"🌊 La estructura interna bajista es un <b>retroceso dentro del régimen alcista</b>{nivel}."
    if c["regimen"] == "bajista" and interna == "alcista":
        return f"🌊 La estructura interna alcista es un <b>rebote dentro del régimen bajista</b>{nivel}."
    if c["regimen"] == interna:
        return f"🌊 Estructura interna y régimen de fondo alineados: <b>{interna}</b>."
    return ""


def linea_ciclo(c):
    if not c:
        return ""
    return f"🌐 Régimen de fondo: <b>{c['regimen'].capitalize()}</b> | Fase: {c['icono']} <b>{c['fase']}</b>"


def texto_ciclo(c):
    if not c:
        return "🌐 <b>Régimen de fondo</b>\nSin datos suficientes esta semana."
    pct = lambda x: f"{x:+.1f}%" if x is not None else "—"
    ep = c["est_principal"]
    lineas = [
        "🌐 <b>Régimen de fondo (semanas)</b>",
        f"Régimen: <b>{c['regimen'].capitalize()}</b> | Fase: {c['icono']} <b>{c['fase']}</b>",
        c["lectura"],
        "",
    ]
    d = c["detalle"]
    if d and c["regimen"] == "alcista":
        lineas.append(f"Máximo del régimen: ${d['extremo']:,.0f} ({_f(d['extremo_fecha'])}) → retroceso {d['movimiento']:.1f}%")
        lineas.append(f"Piso del retroceso: ${d['contra_extremo']:,.0f} → recuperación {d['recupera']:+.1f}%")
    elif d and c["regimen"] == "bajista":
        lineas.append(f"Mínimo del régimen: ${d['extremo']:,.0f} ({_f(d['extremo_fecha'])}) → rebote {d['movimiento']:+.1f}%")
        lineas.append(f"Techo del rebote: ${d['contra_extremo']:,.0f} → caída {d['recupera']:.1f}%")
    if c["nivel_choch"]:
        lado = "bajo" if c["regimen"] == "alcista" else "sobre"
        lineas.append(f"📍 Cambio de régimen si cierra {lado} ${c['nivel_choch']:,.0f} (CHoCH principal)")
    lineas.append(f"Precio 30 días: {pct(c['ret30'])}")
    lineas.append(f"Fear &amp; Greed promedio: 7 días {c['fg7']:.0f} | 30 días {c['fg30']:.0f} | 90 días {c['fg90']:.0f}")
    lineas.append(f"Días en miedo extremo (30 d): {c['ext_miedo_30']} | en euforia extrema: {c['ext_euforia_30']}")
    if c["funding_30d"] is not None:
        lineas.append(f"Funding promedio 30 días: {c['funding_30d'] * 100:+.4f}%")
    if c["noticias_prom"] is not None:
        lineas.append(f"Noticias promedio de la semana: {c['noticias_prom']:+.0f}")
    ultimo = ep.get("ultimo_evento")
    if ultimo:
        lineas.append(f"Último evento principal (swing {SWING_PRINCIPAL}): {ultimo['tipo']} en ${ultimo['nivel']:,.0f} ({_f(ultimo['t'])})")
    return "\n".join(lineas)
