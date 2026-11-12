"""
Como Estamos - Motor de escenarios (Fase 5)

Cruza ESTRUCTURA + SENTIMIENTO + REACCIÓN DEL PRECIO según la matriz:

| Estructura | Sentimiento   | Reacción del precio                 | Lectura                               |
|------------|---------------|-------------------------------------|---------------------------------------|
| Bajista    | Muy negativo  | Sigue haciendo mínimos              | Continuación, no hacer nada           |
| Bajista    | Muy negativo  | Deja de caer / barre y recupera     | Posible suelo, sesgo alcista          |
| Alcista    | Positivo      | Sigue haciendo máximos              | Tendencia sana, a favor               |
| Alcista    | Muy positivo  | No logra nuevos máximos             | Posible distribución, alerta bajista  |
| Alcista    | Negativo      | Aguanta                             | Fuerza, buscar compra en retroceso    |
| Bajista    | Positivo      | No reacciona al alza                | Debilidad, evitar compras             |

Y suma las 5 señales del caso julio-agosto 2026 para decidir la intensidad:
1. Miedo (o euforia) persistente
2. Noticias extremas sin seguimiento del precio
3. Consenso en contra (lo que espera la gente)
4. Posicionamiento cargado (combustible para el squeeze)
5. Gatillo técnico (barrido + recuperación o CHoCH)

Niveles: 🟡 Vigilancia · 🟠 Preparación · 🔴 Confirmación
"""

from datetime import datetime, timezone

VENTANA = 5  # días hacia atrás para buscar extremos y reacciones (ajustable)

MUY_NEG, NEG, POS, MUY_POS = -60, -25, 25, 60


def fecha_iso(vela):
    return datetime.fromtimestamp(int(vela[0]), tz=timezone.utc).date().isoformat()


def _serie_sentimiento(velas, fg_hist, historial, sent_hoy):
    """Sentimiento de los últimos VENTANA días (el último es hoy).
    Usa el historial guardado; si falta un día, usa el Fear & Greed como aproximación."""
    por_fecha = {d["fecha"]: d for d in historial}
    serie = []
    n = min(VENTANA, len(velas))
    for i in range(n - 1, 0, -1):  # días anteriores, del más viejo al más nuevo
        reg = por_fecha.get(fecha_iso(velas[-1 - i]))
        if reg and reg.get("sentimiento") is not None:
            serie.append(reg["sentimiento"])
        elif len(fg_hist) > i:
            serie.append(fg_hist[-1 - i][0] * 2 - 100)
    serie.append(sent_hoy)
    return [round(x) for x in serie]


def absorcion(velas, sent_hoy, noticias_hoy):
    """
    El corazón de la tesis: ¿cómo reaccionó HOY el precio frente a las noticias?
    +100 = les llevó la contraria (absorbió las malas / rechazó las buenas)
    -100 = las siguió.
    Mira tres cosas de la vela: en qué parte de su rango cerró, cuánto cambió en el día
    y si marcó un nuevo extremo de cierre de los últimos días. Devuelve None en días sin
    noticias claramente malas o buenas.
    """
    if len(velas) < VENTANA + 2:
        return None
    hoy, ayer = velas[-1], velas[-2]
    h, l, c = float(hoy[2]), float(hoy[3]), float(hoy[4])
    malas = sent_hoy <= NEG or (noticias_hoy is not None and noticias_hoy <= -40)
    buenas = sent_hoy >= POS or (noticias_hoy is not None and noticias_hoy >= 40)
    if not (malas or buenas):
        return None
    pos = (c - l) / (h - l) if h > l else 0.5
    cambio = (c / float(ayer[4]) - 1) * 100
    previas = [float(v[4]) for v in velas[-1 - VENTANA:-1]]
    signo = 1 if malas else -1
    extremo = c < min(previas) if malas else c > max(previas)
    puntaje = signo * (pos - 0.5) * 100 + max(-30, min(30, signo * cambio * 15)) + (-20 if extremo else 20)
    return {"puntaje": max(-100, min(100, round(puntaje))), "tipo": "malas" if malas else "buenas",
            "pos_rango": pos, "cambio": cambio, "extremo": extremo}


def texto_absorcion(a):
    if not a:
        return ""
    p = a["puntaje"]
    if a["tipo"] == "malas":
        lectura = ("el precio <b>absorbió</b> las malas noticias" if p >= 30 else
                   "el precio <b>siguió</b> a las malas noticias" if p <= -30 else "reacción mixta a las malas noticias")
        extremo = "nuevo mínimo de cierre" if a["extremo"] else "sin nuevo mínimo de cierre"
    else:
        lectura = ("el precio <b>no reaccionó</b> a las buenas noticias" if p >= 30 else
                   "el precio <b>siguió</b> a las buenas noticias" if p <= -30 else "reacción mixta a las buenas noticias")
        extremo = "nuevo máximo de cierre" if a["extremo"] else "sin nuevo máximo de cierre"
    zona = "parte alta" if a["pos_rango"] >= 0.66 else "parte baja" if a["pos_rango"] <= 0.33 else "mitad"
    return (f"🥊 Precio vs. noticias: <b>{p:+d}</b> → {lectura} "
            f"(cerró en la {zona} del rango del día, {a['cambio']:+.1f}%, {extremo})")


def evaluar(velas, est, fg_hist, sent_hoy, noticias_hoy, sesgo_pos, sesgo_exp, historial):
    """
    velas: velas cerradas [t, o, h, l, c]
    Devuelve un diccionario con el escenario, el nivel de alerta, la lectura y las confluencias.
    """
    hoy = velas[-1]
    cierre, minimo, maximo = float(hoy[4]), float(hoy[3]), float(hoy[2])
    previas = velas[-1 - VENTANA:-1]
    min_prev_cierre = min(float(v[4]) for v in previas)
    max_prev_cierre = max(float(v[4]) for v in previas)
    desde = int(velas[-VENTANA][0])

    eventos_5d = [e["tipo"] for e in est["eventos"] if e["t"] >= desde]
    eventos_hoy = [e["tipo"] for e in est["eventos_hoy"]]

    serie = _serie_sentimiento(velas, fg_hist, historial, sent_hoy)
    sent_min, sent_max = min(serie), max(serie)
    fg_5d = [v for v, _ in fg_hist[-VENTANA:]]

    noticias_5d = [d.get("noticias") for d in historial[-(VENTANA - 1):] if d.get("noticias") is not None]
    if noticias_hoy is not None:
        noticias_5d.append(noticias_hoy)

    # ── Reacción del precio ──
    nuevo_minimo = "BOS bajista" in eventos_hoy or cierre < min_prev_cierre
    nuevo_maximo = "BOS alcista" in eventos_hoy or cierre > max_prev_cierre
    barrido_min = "Barrido de mínimo" in eventos_5d
    barrido_max = "Barrido de máximo" in eventos_5d
    choch_alc = "CHoCH alcista" in eventos_5d
    choch_baj = "CHoCH bajista" in eventos_5d

    # "Dejó de caer": desde el día de sentimiento más negativo, ningún cierre por debajo de su mínimo
    i_peor = serie.index(sent_min)
    dia_peor = velas[-len(serie) + i_peor]
    dejo_de_caer = barrido_min or choch_alc or (
        i_peor < len(serie) - 1
        and all(float(v[4]) >= float(dia_peor[3]) for v in velas[-len(serie) + i_peor + 1:])
        and not nuevo_minimo
    )
    i_mejor = serie.index(sent_max)
    dia_mejor = velas[-len(serie) + i_mejor]
    no_nuevos_max = barrido_max or choch_baj or (
        i_mejor < len(serie) - 1
        and all(float(v[4]) <= float(dia_mejor[2]) for v in velas[-len(serie) + i_mejor + 1:])
        and not nuevo_maximo
    )

    # ── Confluencias para un SUELO (sesgo alcista) ──
    conf_suelo = [
        ("Miedo persistente (F&G ≤ 30 en 3 de 5 días)", sum(1 for v in fg_5d if v <= 30) >= 3),
        ("Noticias muy negativas sin seguimiento", any(n <= -40 for n in noticias_5d) and not nuevo_minimo),
        ("La gente espera bajada (consenso en contra)", sesgo_exp is not None and sesgo_exp <= -30),
        ("Multitud cargada en short (combustible)", sesgo_pos is not None and sesgo_pos <= -40),
        ("Gatillo: barrido de mínimo o CHoCH alcista", barrido_min or choch_alc),
    ]
    # ── Confluencias para un TECHO (sesgo bajista) ──
    conf_techo = [
        ("Euforia persistente (F&G ≥ 70 en 3 de 5 días)", sum(1 for v in fg_5d if v >= 70) >= 3),
        ("Noticias muy positivas sin seguimiento", any(n >= 40 for n in noticias_5d) and not nuevo_maximo),
        ("La gente espera subida (consenso en contra)", sesgo_exp is not None and sesgo_exp >= 30),
        ("Multitud cargada en long (combustible)", sesgo_pos is not None and sesgo_pos >= 40),
        ("Gatillo: barrido de máximo o CHoCH bajista", barrido_max or choch_baj),
    ]
    n_suelo = sum(ok for _, ok in conf_suelo)
    n_techo = sum(ok for _, ok in conf_techo)

    negativo = sent_hoy <= NEG or (noticias_hoy is not None and noticias_hoy <= -40)
    muy_negativo = sent_min <= MUY_NEG
    positivo = sent_hoy >= POS or (noticias_hoy is not None and noticias_hoy >= 40)
    muy_positivo = sent_max >= MUY_POS
    multitud_long = sesgo_pos is not None and sesgo_pos >= 15
    multitud_short = sesgo_pos is not None and sesgo_pos <= -15

    t = est["tendencia"]
    nivel, titulo, lectura, confluencias = "⚪", "Sin señal", "", None

    if t == "bajista":
        if negativo or muy_negativo:
            confluencias = ("suelo", conf_suelo, n_suelo)
            if choch_alc and n_suelo >= 3:
                nivel, titulo = "🔴", "CONFIRMACIÓN — Posible suelo"
                lectura = "Pánico + CHoCH alcista: el precio le llevó la contraria a las noticias. Sesgo alcista."
            elif dejo_de_caer and n_suelo >= 3:
                nivel, titulo = "🟠", "PREPARACIÓN — Posible suelo"
                lectura = "Noticias negativas y el precio ya no cae. Esperar CHoCH alcista para confirmar."
            elif dejo_de_caer and multitud_long:
                # "Barrido" solo si la estructura marcó un barrido de mínimo (misma regla en todo el reporte)
                nivel = "🟡"
                titulo = ("VIGILANCIA — Barrido sin capitulación" if barrido_min
                          else "VIGILANCIA — Pausa sin capitulación")
                lectura = (("El precio barrió un mínimo y recuperó" if barrido_min else "El precio dejó de caer")
                           + ", pero la gente sigue en long: no hay pánico real todavía. "
                           "Si pierde el mínimo, esos longs son combustible para más caída.")
            elif dejo_de_caer:
                nivel, titulo = "🟡", "VIGILANCIA — El precio dejó de caer"
                lectura = "Sentimiento negativo y el precio aguanta. Faltan confluencias para hablar de suelo."
            elif nuevo_minimo:
                nivel, titulo = "⚪", "Continuación bajista"
                lectura = "Noticias negativas y el precio sigue haciendo mínimos. No hacer nada contra la tendencia."
            elif muy_negativo:
                nivel, titulo = "🟡", "VIGILANCIA — Pánico sin reacción todavía"
                lectura = "Sentimiento extremo en estructura bajista. Esperar a ver si el precio deja de caer."
            else:
                nivel, titulo = "⚪", "Bajista con noticias negativas"
                lectura = "Sin extremos ni reacción clara del precio."
            if multitud_long and nivel != "🟡":
                lectura += " Ojo: la multitud sigue en long, no hay capitulación."
        elif positivo and not (choch_alc or nuevo_maximo):
            nivel, titulo = "⚠️", "DEBILIDAD — Noticias positivas sin reacción"
            lectura = "Hay buenas noticias y el precio no sube. Evitar compras."
        else:
            titulo, lectura = "Bajista sin extremos", "Sentimiento sin extremos: dejar que la estructura mande."

    elif t == "alcista":
        if positivo or muy_positivo:
            confluencias = ("techo", conf_techo, n_techo)
            if choch_baj and n_techo >= 3:
                nivel, titulo = "🔴", "CONFIRMACIÓN — Posible techo"
                lectura = "Euforia + CHoCH bajista: distribución confirmada. Sesgo bajista."
            elif no_nuevos_max and n_techo >= 3:
                nivel, titulo = "🟠", "PREPARACIÓN — Posible distribución"
                lectura = "Noticias muy positivas y el precio ya no hace máximos. Esperar CHoCH bajista."
            elif no_nuevos_max and muy_positivo:
                nivel, titulo = "🟡", "VIGILANCIA — Euforia sin nuevos máximos"
                lectura = "Mucho optimismo y el precio se frena. Proteger ganancias."
            elif nuevo_maximo:
                nivel, titulo = "🟢", "Tendencia sana"
                lectura = "Noticias positivas y el precio sigue haciendo máximos. A favor de la tendencia."
            else:
                nivel, titulo = "🟢", "Alcista con optimismo"
                lectura = "Sin señales de agotamiento todavía."
            if multitud_short and nivel == "🟢":
                lectura += " La multitud está en short: combustible extra para subir."
        elif negativo and not choch_baj:
            nivel, titulo = "🟢", "FUERZA — Noticias negativas y el precio aguanta"
            lectura = "La estructura alcista resiste las malas noticias. Buscar compra en retroceso."
        else:
            titulo, lectura = "Alcista sin extremos", "Sentimiento sin extremos: dejar que la estructura mande."
    else:
        titulo, lectura = "Rango", "Sin estructura clara todavía."

    # Las confluencias se muestran SIEMPRE (no solo en días de sentimiento extremo)
    if confluencias is None and t == "bajista":
        confluencias = ("suelo", conf_suelo, n_suelo)
    elif confluencias is None and t == "alcista":
        confluencias = ("techo", conf_techo, n_techo)

    # Niveles clave
    niveles = {}
    if est.get("ultimo_max") and not est["ultimo_max"]["roto"]:
        niveles["arriba"] = est["ultimo_max"]["precio"]
    if est.get("ultimo_min") and not est["ultimo_min"]["roto"]:
        niveles["abajo"] = est["ultimo_min"]["precio"]

    # Qué confirma un giro y qué confirma que la tendencia sigue.
    # Si el último swing ya está roto, se usa el extremo del tramo en curso.
    giro = continuacion = None
    if t == "bajista":
        if "arriba" in niveles:
            giro = (niveles["arriba"], "sobre", "CHoCH alcista")
        if "abajo" in niveles:
            continuacion = (niveles["abajo"], "bajo", "BOS bajista")
        else:
            desde_t = est["ultimo_max"]["t"] if est.get("ultimo_max") else int(velas[-10][0])
            tramo = [float(v[3]) for v in velas if int(v[0]) >= desde_t] or [minimo]
            continuacion = (min(tramo), "bajo", "nuevo mínimo del tramo")
    elif t == "alcista":
        if "abajo" in niveles:
            giro = (niveles["abajo"], "bajo", "CHoCH bajista")
        if "arriba" in niveles:
            continuacion = (niveles["arriba"], "sobre", "BOS alcista")
        else:
            desde_t = est["ultimo_min"]["t"] if est.get("ultimo_min") else int(velas[-10][0])
            tramo = [float(v[2]) for v in velas if int(v[0]) >= desde_t] or [maximo]
            continuacion = (max(tramo), "sobre", "nuevo máximo del tramo")

    return {"nivel": nivel, "titulo": titulo, "lectura": lectura, "confluencias": confluencias,
            "niveles": niveles, "tendencia": t, "serie": serie,
            "absorcion": absorcion(velas, sent_hoy, noticias_hoy),
            "giro": giro, "continuacion": continuacion}


def texto_niveles(r):
    """Los dos niveles que importan: el que confirma un giro y el que confirma que la tendencia sigue."""
    lineas = []
    if r.get("giro"):
        precio, lado, nombre = r["giro"]
        lineas.append(f"{'⬆️' if lado == 'sobre' else '⬇️'} Giro: cierre {lado} <b>${precio:,.0f}</b> ({nombre})")
    if r.get("continuacion"):
        precio, lado, nombre = r["continuacion"]
        lineas.append(f"{'⬆️' if lado == 'sobre' else '⬇️'} Continuación: cierre {lado} <b>${precio:,.0f}</b> ({nombre})")
    return lineas


def texto_escenario(r):
    lineas = [f"🎯 <b>Escenario: {r['nivel']} {r['titulo']}</b>", r["lectura"]]
    reaccion = texto_absorcion(r.get("absorcion"))
    if reaccion:
        lineas.append(reaccion)
    if r["confluencias"]:
        tipo, lista, n = r["confluencias"]
        lineas.append(f"\nConfluencias de {tipo}: <b>{n} de 5</b>")
        lineas += [f"{'✅' if ok else '▫️'} {nombre}" for nombre, ok in lista]
    niveles = texto_niveles(r)
    if niveles:
        lineas.append("\n📍 <b>Niveles que deciden</b>")
        lineas += niveles
    lineas.append("Sentimiento últimos días: " + " → ".join(f"{s:+d}" for s in r["serie"]))
    if r["nivel"] in ("🟠", "🔴"):
        lineas.append("\n💬 <b>Alerta importante: pégale este reporte a Claude para el análisis a fondo.</b>")
    return "\n".join(lineas)
