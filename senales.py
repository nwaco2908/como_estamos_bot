"""
Como Estamos - Marcador de señales: ¿qué pasó después de cada señal del bot?

Usa la memoria (historial.json) para revisar cada señal que el bot dio y medir qué hizo el
precio después. Es la forma de saber, con datos propios, qué escenarios aciertan y cuáles no.

- Una "señal" es el PRIMER día en que aparece un escenario o una fase del régimen
  (si se repite varios días seguidos cuenta una sola vez).
- Cada señal tiene un sesgo esperado: +1 (el precio debería subir), -1 (bajar) o 0 (solo vigilar).
- Se mide el cambio del precio de cierre HORIZONTE días después.
"""

from datetime import datetime, timezone

HORIZONTE = 7

# Sesgo esperado de cada fase del régimen y de cada escenario diario.
# Revisado con el backtest 2018–2026 (nov 2026): a las señales que el historial contradijo con
# claridad (acierto ≤ 47% con 10+ casos) se les QUITÓ la dirección (0 = solo informativa).
# No se invirtieron: darles la vuelta sería ajustar el bot al pasado.
SESGO_FASE = {
    "Retroceso absorbido": 1, "Reanudación alcista": 1, "Rebote con miedo": 1,
    "Cambio a régimen alcista": 1, "Tendencia alcista sana": 1,
    "Reanudación bajista": -1, "Cambio a régimen bajista": -1,
    # Sin dirección
    "Retroceso con miedo": 0,     # el precio siguió cayendo: es fase de ESPERAR
    "Capitulación": 0,            # atrapar un cuchillo
    "Euforia en máximos": 0,      # en BTC la euforia fue impulso, no techo
    "Rebote agotado": 0, "Rebote con optimismo": 0, "Tendencia bajista": 0,
}
SESGO_ESC = {
    "CONFIRMACIÓN — Posible suelo": 1, "PREPARACIÓN — Posible suelo": 1,
    "VIGILANCIA — El precio dejó de caer": 1, "VIGILANCIA — Pánico sin reacción todavía": 1,
    "Tendencia sana": 1,
    "CONFIRMACIÓN — Posible techo": -1, "PREPARACIÓN — Posible distribución": -1,
    "DEBILIDAD — Noticias positivas sin reacción": -1,
    # Sin dirección
    "FUERZA — Noticias negativas y el precio aguanta": 0,
    "VIGILANCIA — Euforia sin nuevos máximos": 0,
    "Continuación bajista": 0,
    "VIGILANCIA — Barrido sin capitulación": 0, "VIGILANCIA — Pausa sin capitulación": 0,
}


def _sin_icono(texto):
    """'🟡 VIGILANCIA — …' → 'VIGILANCIA — …'"""
    if not texto:
        return None
    partes = texto.split(" ", 1)
    return partes[1] if len(partes) == 2 and not partes[0][0].isalnum() else texto


def _fecha(vela):
    return datetime.fromtimestamp(int(vela[0]), tz=timezone.utc).date().isoformat()


def episodios(hist):
    """Primer día de cada señal: [(fecha, tipo, nombre, sesgo)], de la más vieja a la más nueva."""
    salida = []
    for clave, tipo, sesgos in (("escenario", "Escenario", SESGO_ESC), ("fase_regimen", "Fase", SESGO_FASE)):
        previo = None
        for d in sorted(hist, key=lambda x: x.get("fecha", "")):
            nombre = _sin_icono(d.get(clave))
            if nombre and nombre != previo and nombre in sesgos:
                salida.append((d["fecha"], tipo, nombre, sesgos[nombre]))
            if nombre:
                previo = nombre
    salida.sort(key=lambda x: x[0])
    return salida


def evaluar_senales(hist, velas, horizonte=HORIZONTE):
    """Añade a cada señal qué hizo el precio después."""
    fechas = [_fecha(v) for v in velas]
    cierres = [float(v[4]) for v in velas]
    indice = {f: i for i, f in enumerate(fechas)}
    ultimo = len(velas) - 1
    resultado = []
    for fecha, tipo, nombre, sesgo in episodios(hist):
        i = indice.get(fecha)
        if i is None or i >= ultimo:
            continue  # la señal es de hoy: todavía no hay nada que medir
        j = min(i + horizonte, ultimo)
        cambio = (cierres[j] / cierres[i] - 1) * 100
        completa = (j - i) == horizonte
        acierto = None if sesgo == 0 else (cambio * sesgo > 0)
        resultado.append({"fecha": fecha, "tipo": tipo, "nombre": nombre, "sesgo": sesgo,
                          "cambio": cambio, "dias": j - i, "completa": completa, "acierto": acierto})
    return resultado


def texto_marcador(hist, velas, max_lineas=8, horizonte=HORIZONTE):
    senales = evaluar_senales(hist, velas, horizonte)
    if not senales:
        return ""
    lineas = ["📊 <b>¿Cómo les fue a las señales?</b>",
              f"<i>Cambio del precio {horizonte} días después del primer día de cada señal</i>"]
    for s in reversed(senales[-max_lineas:]):
        f = datetime.fromisoformat(s["fecha"])
        espera = {1: "esperaba ⬆️", -1: "esperaba ⬇️", 0: "sin dirección"}[s["sesgo"]]
        plazo = f"a {s['dias']} d" if s["completa"] else f"en {s['dias']} d, en curso"
        # El veredicto solo se da cuando la señal cumplió su plazo completo
        marca = "" if (s["acierto"] is None or not s["completa"]) else (" ✅" if s["acierto"] else " ❌")
        lineas.append(f"{f:%d %b} · {s['tipo']}: {s['nombre']} ({espera}) → {s['cambio']:+.1f}% {plazo}{marca}")
    cerradas = [s for s in senales if s["completa"] and s["acierto"] is not None]
    if cerradas:
        ok = sum(1 for s in cerradas if s["acierto"])
        lineas.append(f"Marcador acumulado: <b>{ok} de {len(cerradas)}</b> señales con dirección acertaron "
                      f"({ok / len(cerradas) * 100:.0f}%)")
        if len(cerradas) < 10:
            lineas.append("<i>Todavía son muy pocas señales para sacar conclusiones.</i>")
    else:
        lineas.append("<i>Aún no hay señales con dirección que hayan cumplido su plazo.</i>")
    return "\n".join(lineas)
