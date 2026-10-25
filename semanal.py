"""
Como Estamos - Resumen semanal

Se envía el domingo después del cierre diario (7:05 p. m. hora Colombia):
- Cómo se movió BTC en la semana (apertura, cierre, máximo, mínimo, variación).
- Eventos de estructura de la semana (BOS, CHoCH, barridos).
- Sentimiento de la semana: Fear & Greed día a día y tono de las noticias.
- Titulares y narrativas que dominaron la semana.
- Cómo cambió el posicionamiento (longs vs shorts) y qué espera la gente ahora.
"""

from datetime import datetime, timezone

from noticias import etiqueta_sentimiento

DIAS = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]


def _f(ts):
    return datetime.fromtimestamp(int(ts), tz=timezone.utc)


def _esc(t):
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def texto_semanal(velas, est, fg_hist, noticias_sem, pos, exp_texto):
    semana = velas[-7:]
    apertura = float(semana[0][1])
    cierre = float(semana[-1][4])
    maximo = max(float(v[2]) for v in semana)
    minimo = min(float(v[3]) for v in semana)
    cambio = (cierre / apertura - 1) * 100
    inicio, fin = _f(semana[0][0]), _f(semana[-1][0])
    flecha = "🟢" if cambio >= 0 else "🔴"

    lineas = [
        "<b>Como Estamos — Resumen semanal</b>",
        f"{inicio:%d %b} al {fin:%d %b %Y}",
        "",
        f"₿ Semana: <b>{flecha} {cambio:+.2f}%</b>",
        f"Apertura ${apertura:,.0f} → Cierre <b>${cierre:,.0f}</b>",
        f"Máximo ${maximo:,.0f} | Mínimo ${minimo:,.0f} (rango {(maximo / minimo - 1) * 100:.1f}%)",
    ]

    # Recorrido diario
    recorrido = []
    for v in semana:
        c = (float(v[4]) / float(v[1]) - 1) * 100
        recorrido.append(f"{DIAS[_f(v[0]).weekday()]} {'🟢' if c >= 0 else '🔴'}")
    lineas.append(" ".join(recorrido))

    # Estructura
    desde = int(semana[0][0])
    eventos = [e for e in est["eventos"] if e["t"] >= desde]
    lineas += ["", f"🧭 Estructura al cierre: <b>{est['tendencia'].capitalize()}</b>"]
    if eventos:
        for e in eventos:
            lineas.append(f"• {DIAS[_f(e['t']).weekday()]}: {e['tipo']} en ${e['nivel']:,.0f}")
    else:
        lineas.append("Sin BOS, CHoCH ni barridos esta semana")

    # Sentimiento
    lineas += ["", "🧠 <b>Sentimiento de la semana</b>"]
    if fg_hist:
        valores = [v for v, _ in fg_hist]
        prom = sum(valores) / len(valores)
        lineas.append(
            f"Fear &amp; Greed: promedio {prom:.0f} (de {min(valores)} a {max(valores)}), "
            f"hoy {valores[-1]} {fg_hist[-1][1]}"
        )
        lineas.append("Día a día: " + " → ".join(str(v) for v in valores))
    if noticias_sem["total"]:
        lineas.append(
            f"Noticias: {noticias_sem['puntaje']:+d} {etiqueta_sentimiento(noticias_sem['puntaje'])} "
            f"({noticias_sem['neg']} 🔴 / {noticias_sem['pos']} 🟢 de {noticias_sem['total']})"
        )
        if noticias_sem["narrativas"]:
            top = sorted(noticias_sem["narrativas"].items(), key=lambda x: -x[1])[:5]
            lineas.append("⚠️ Narrativas: " + ", ".join(f"{k} ×{v}" for k, v in top))
        if noticias_sem["peores"]:
            lineas.append("\nTitulares negativos de la semana:")
            lineas += [f"🔴 {_esc(t['titulo'])}" for t in noticias_sem["peores"]]
        if noticias_sem["mejores"]:
            lineas.append("\nTitulares positivos de la semana:")
            lineas += [f"🟢 {_esc(t['titulo'])}" for t in noticias_sem["mejores"]]

    # Posicionamiento
    ls = (pos or {}).get("long_short")
    f = (pos or {}).get("funding")
    if ls or f:
        lineas += ["", "⚖️ <b>Posicionamiento</b>"]
        if ls and ls.get("hace_7d"):
            cambio_ls = "más long" if ls["ratio"] > ls["hace_7d"] else "más short"
            lineas.append(f"Ratio long/short: {ls['hace_7d']:.2f} → {ls['ratio']:.2f} (la gente se puso {cambio_ls})")
        elif ls:
            lineas.append(f"Ratio long/short: {ls['ratio']:.2f}")
        if f and f.get("promedio_7d") is not None:
            lineas.append(f"Funding promedio de la semana: {f['promedio_7d'] * 100:+.4f}%")

    # Expectativas actuales
    if exp_texto:
        lineas += ["", exp_texto]

    return "\n".join(lineas)
