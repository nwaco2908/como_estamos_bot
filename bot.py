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
from noticias import (analizar_noticias, etiqueta_sentimiento, obtener_titulares, puntaje_fiable,
                      sentimiento_combinado, texto_noticias)
from posicionamiento import etiqueta_multitud, obtener_posicionamiento, sesgo_multitud, texto_posicionamiento
from expectativas import obtener_expectativas, sesgo_expectativas, texto_expectativas
from semanal import texto_semanal
from escenarios import evaluar, fecha_iso, texto_escenario, texto_niveles
import historial
from ciclo import linea_ciclo, lectura_combinada, obtener_ciclo, texto_ciclo
from macro import obtener_macro, origen_miedo, texto_macro
from senales import texto_marcador

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


MIN_VELAS = 400  # el régimen de fondo (swing 25) necesita bastante historia


def _historial_bitget(velas, objetivo=MIN_VELAS):
    """Bitget entrega pocas velas por consulta: pide páginas hacia atrás hasta tener suficientes."""
    base = ("https://api.bitget.com/api/v2/mix/market/history-candles"
            "?symbol=BTCUSDT&productType=usdt-futures&limit=200&granularity=")
    for gran in ("1Dutc", "1D"):
        extra, intentos = [], 0
        mas_vieja = velas[0][0]
        while len(velas) + len(extra) < objetivo and intentos < 5:
            intentos += 1
            try:
                data = get_json(f"{base}{gran}&endTime={(mas_vieja - 1) * 1000}")
            except Exception as e:
                print("Historia Bitget no disponible:", e)
                break
            if data.get("code") != "00000" or not data.get("data"):
                break
            pagina = sorted(([int(v[0]) // 1000, v[1], v[2], v[3], v[4]] for v in data["data"]),
                            key=lambda v: v[0])
            pagina = [v for v in pagina if v[0] < mas_vieja]
            if not pagina:
                break
            extra = pagina + extra
            mas_vieja = pagina[0][0]
        if extra:
            return extra + velas
    return velas


def _completar_con_kraken(velas):
    """Si aún faltan velas, completa la historia más vieja con Kraken (solo para el régimen de fondo)."""
    try:
        kr = velas_kraken()
    except Exception as e:
        print("Kraken no disponible para completar historia:", e)
        return velas
    primera = velas[0][0]
    viejas = [v for v in kr if int(v[0]) < primera]
    return viejas + velas


def velas_diarias_btc():
    """Bitget primero (con historia extendida); si falla, Kraken. Devuelve solo velas cerradas."""
    global FUENTE
    try:
        velas = velas_bitget()
        if len(velas) < 50:
            raise RuntimeError("Bitget devolvió muy pocas velas")
        FUENTE = "Bitget BTCUSDT perpetuo"
        if len(velas) < MIN_VELAS:
            velas = _historial_bitget(velas)
        if len(velas) < MIN_VELAS:
            velas = _completar_con_kraken(velas)
            FUENTE = "Bitget BTCUSDT perpetuo (historia antigua: Kraken)"
    except Exception as e:
        print("Bitget no disponible, uso Kraken:", e)
        velas = velas_kraken()
        FUENTE = "Kraken BTC/USD (respaldo)"
    velas = solo_cerradas(velas)
    print(f"Velas diarias cargadas: {len(velas)} ({FUENTE})")
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


def _fecha_corta(iso):
    return datetime.fromisoformat(iso).strftime("%d %b")


def comparar_con_anterior(previo, hoy):
    """
    Qué cambió frente al último reporte guardado.
    Devuelve (alertas, cambios): las alertas van arriba del mensaje; los cambios, en su propio bloque.
    """
    if not previo:
        return [], []
    alertas, cambios = [], []

    definidos = ("alcista", "bajista")
    regimen_claro = previo.get("regimen") in definidos and hoy.get("regimen") in definidos
    if regimen_claro and previo["regimen"] != hoy["regimen"]:
        alertas.append(f"🚨 <b>CAMBIO DE RÉGIMEN</b>: {previo['regimen']} → {hoy['regimen']}")
    fase_a, fase_h = previo.get("fase_regimen"), hoy.get("fase_regimen")
    if fase_a and fase_h and fase_a != fase_h:
        cambios.append(f"Fase del régimen: {fase_a} → {fase_h}")
        if regimen_claro:  # si el régimen quedó sin definir (p. ej. faltaron velas) no se alerta
            alertas.append(f"🔔 <b>Cambio de fase del régimen</b>: {fase_a} → {fase_h}")

    esc_a, esc_h = previo.get("escenario"), hoy.get("escenario")
    if esc_a and esc_h and esc_a != esc_h:
        cambios.append(f"Escenario: {esc_a} → {esc_h}")
        if esc_h.startswith(("🟠", "🔴")):
            alertas.append(f"🔔 <b>Nueva alerta</b>: {esc_h}")

    if previo.get("tendencia") and previo["tendencia"] != hoy.get("tendencia"):
        cambios.append(f"Estructura interna: {previo['tendencia']} → {hoy['tendencia']}")
    if previo.get("confluencias") and hoy.get("confluencias") and previo["confluencias"] != hoy["confluencias"]:
        cambios.append(f"Confluencias: {previo['confluencias']} → {hoy['confluencias']}")
    if previo.get("sentimiento") is not None and hoy.get("sentimiento") is not None:
        et_a, et_h = etiqueta_sentimiento(previo["sentimiento"]), etiqueta_sentimiento(hoy["sentimiento"])
        if et_a != et_h:
            cambios.append(f"Sentimiento: {et_a} ({previo['sentimiento']:+d}) → {et_h} ({hoy['sentimiento']:+d})")
    if previo.get("sesgo_multitud") is not None and hoy.get("sesgo_multitud") is not None:
        mu_a, mu_h = etiqueta_multitud(previo["sesgo_multitud"]), etiqueta_multitud(hoy["sesgo_multitud"])
        if mu_a != mu_h:
            cambios.append(f"Multitud: {mu_a} → {mu_h}")
    return alertas, cambios


def reporte_diario(velas, est, fg_hist, pos, sesgo_pos, hist, avisos=None):
    avisos = list(avisos or [])
    ultima, anterior = velas[-1], velas[-2]
    fecha = datetime.fromtimestamp(int(ultima[0]), tz=timezone.utc).strftime("%d %b %Y")
    cierre, maximo, minimo = float(ultima[4]), float(ultima[2]), float(ultima[3])
    cambio = (cierre / float(anterior[4]) - 1) * 100

    fg_valor, fg_texto = fg_hist[-1]
    titulares, fallidas = obtener_titulares(horas=24)
    noticias = analizar_noticias(titulares)
    # Si hay muy pocos titulares (p. ej. Google News no respondió), las noticias NO cuentan hoy:
    # un "0" por falta de datos no es lo mismo que un día neutral.
    noticias_ok = puntaje_fiable(noticias)
    if noticias_ok is None:
        avisos.append(f"noticias (solo {noticias['total']} titulares: hoy no cuentan para el sentimiento)")
    combinado = sentimiento_combinado(fg_valor, noticias_ok)
    exp = obtener_expectativas(cierre, titulares)
    sesgo_exp = sesgo_expectativas(exp)
    avisos += [f"expectativas ({f})" for f in exp.get("fallos") or []]

    ls = (pos or {}).get("long_short") or {}
    fu = (pos or {}).get("funding") or {}
    if not ls:
        avisos.append("posicionamiento (ningún exchange entregó el ratio de cuentas)")
    elif len(ls.get("fuentes") or []) < 2:
        avisos.append(f"posicionamiento (ratio de cuentas de un solo exchange: {', '.join(ls.get('fuentes') or [])})")

    previos = [d for d in hist if d.get("fecha") != fecha_iso(ultima)]
    esc = evaluar(velas, est, fg_hist, combinado, noticias_ok, sesgo_pos, sesgo_exp, previos)

    # Régimen de fondo (la marea) y contexto macro
    ult_noticias = [d.get("noticias") for d in previos[-6:] if d.get("noticias") is not None]
    if noticias_ok is not None:
        ult_noticias.append(noticias_ok)
    noticias_prom = sum(ult_noticias) / len(ult_noticias) if ult_noticias else None
    ciclo = obtener_ciclo(velas, noticias_prom)
    if not ciclo:
        avisos.append("régimen de fondo")
    try:
        mac = obtener_macro(dias=1)
        avisos += [f"macro ({f})" for f in mac.get("fallos") or []]
    except Exception as e:
        print("Macro no disponible:", e)
        mac = None
        avisos.append("macro")

    # Memoria: guardamos el día para los próximos análisis
    conf = esc.get("confluencias")
    registro = {
        "fecha": fecha_iso(ultima),
        "cierre": round(cierre, 1), "maximo": round(maximo, 1), "minimo": round(minimo, 1),
        "tendencia": est["tendencia"],
        "eventos": [e["tipo"] for e in est["eventos_hoy"]],
        "fear_greed": fg_valor,
        "noticias": noticias_ok,              # None si ese día no eran fiables
        "noticias_crudo": noticias["puntaje"],
        "n_titulares": noticias["total"],
        "narrativas": noticias["narrativas"],
        "resiliencia": len(noticias["resiliencia"]),
        "sentimiento": combinado,
        "absorcion": esc["absorcion"]["puntaje"] if esc.get("absorcion") else None,
        "sesgo_multitud": sesgo_pos,
        "ratio_long_short": round(ls["ratio"], 3) if ls.get("ratio") else None,
        "desvio_long_short": round(ls["desvio"], 1) if ls.get("desvio") is not None else None,
        "fuentes_long_short": ls.get("fuentes"),
        "funding": fu.get("actual"),
        "sesgo_expectativas": sesgo_exp,
        "escenario": f"{esc['nivel']} {esc['titulo']}",
        "confluencias": f"{conf[0]} {conf[2]}/5" if conf else None,
        "giro": [round(esc["giro"][0], 1), *esc["giro"][1:]] if esc.get("giro") else None,
        "continuacion": [round(esc["continuacion"][0], 1), *esc["continuacion"][1:]] if esc.get("continuacion") else None,
        "regimen": ciclo["regimen"] if ciclo else None,
        "fase_regimen": f"{ciclo['icono']} {ciclo['fase']}" if ciclo else None,
        "macro": mac["puntaje"] if mac else None,
        "avisos": avisos,
    }
    try:
        historial.guardar(registro)
    except Exception as e:
        print("No pude guardar el historial:", e)

    previo = previos[-1] if previos else None
    alertas, cambios = comparar_con_anterior(previo, registro)

    flecha = "🟢" if cambio >= 0 else "🔴"
    partes = []
    if alertas:  # lo urgente va en la primera línea: es lo que se ve en la notificación
        if esc["nivel"] not in ("🟠", "🔴"):  # en 🟠/🔴 el propio escenario ya lo pide
            alertas.append("💬 Pégale este reporte a Claude para el análisis a fondo.")
        partes.append("\n".join(alertas))
    partes += [
        f"<b>Como Estamos</b>\nVela del {fecha}",
        f"₿ Cierre: <b>${cierre:,.0f}</b> {flecha} {cambio:+.2f}%\nMáximo: ${maximo:,.0f} | Mínimo: ${minimo:,.0f}",
        texto_escenario(esc),
    ]
    if previo:
        titulo = f"🔄 <b>Frente al reporte anterior</b> ({_fecha_corta(previo['fecha'])})"
        partes.append(titulo + ("\n" + "\n".join(f"• {c}" for c in cambios) if cambios else ": sin cambios"))
    contexto = [x for x in (
        linea_ciclo(ciclo) if ciclo else "",
        lectura_combinada(ciclo, est) if ciclo else "",
        origen_miedo(combinado, noticias_ok, mac),
        ("⚠️ <b>Datos incompletos hoy</b>: " + "; ".join(avisos)) if avisos else "",
    ) if x]
    if contexto:
        partes.append("\n".join(contexto))
    partes += [texto_estructura(est), texto_noticias(noticias, combinado, fg_valor, fg_texto, fallidas)]
    if mac:
        partes.append(texto_macro(mac))
    partes += [texto_posicionamiento(pos, sesgo_pos), texto_expectativas(exp, sesgo_exp),
               f"<i>Fuente: {FUENTE}</i>"]
    return "\n\n".join(partes)


def veredicto_semanal(registro, ciclo, est, registros_semana):
    """El resumen de arriba del semanal: marea, olas, confluencias, niveles y próxima señal."""
    lineas = ["🧾 <b>En resumen</b>"]
    if ciclo:
        lineas.append(f"Marea: régimen <b>{ciclo['regimen']}</b> · fase {ciclo['icono']} <b>{ciclo['fase']}</b>")
    olas = f"Olas: estructura interna <b>{est['tendencia']}</b>"
    if registro and registro.get("escenario"):
        olas += f" · escenario {registro['escenario']}"
    lineas.append(olas)
    if registro and registro.get("confluencias"):
        lineas.append(f"Confluencias de {registro['confluencias']}")
    if registro:
        lineas += texto_niveles({"giro": registro.get("giro"), "continuacion": registro.get("continuacion")})
    if ciclo and ciclo.get("proxima"):
        lineas.append(f"⏭ Próxima señal: {ciclo['proxima']}")
    # Cambios de fase durante la semana
    fases = []
    for d in registros_semana:
        f = d.get("fase_regimen")
        if f and (not fases or fases[-1][1] != f):
            fases.append((d["fecha"], f))
    if len(fases) > 1:
        lineas.append("Fases en la semana: " + " → ".join(f"{f} ({_fecha_corta(fe)})" for fe, f in fases))
    return "\n".join(lineas)


def reporte_semanal(velas, est, fg_hist, pos):
    titulares_sem, _ = obtener_titulares(horas=24 * 7)
    noticias_sem = analizar_noticias(titulares_sem, n=5)
    cierre = float(velas[-1][4])
    exp = obtener_expectativas(cierre, titulares_sem)
    desde = fecha_iso(velas[-7])
    hist = historial.cargar()
    semana = [d for d in hist if d.get("fecha", "") >= desde]
    escenarios_sem = [(d["fecha"], d.get("escenario")) for d in semana if d.get("escenario")]

    # UNA sola cifra de noticias para la semana: el promedio de los puntajes diarios fiables
    ult = [d.get("noticias") for d in semana if d.get("noticias") is not None]
    noticias_prom = sum(ult) / len(ult) if ult else puntaje_fiable(noticias_sem)
    ciclo = obtener_ciclo(velas, noticias_prom)

    avisos = []
    faltan = 7 - len({d["fecha"] for d in semana})
    if faltan > 0:
        avisos.append(f"{faltan} día(s) sin registro en la memoria")
    incompletos = sum(1 for d in semana if d.get("avisos"))
    if incompletos:
        avisos.append(f"{incompletos} día(s) con alguna fuente caída (ver los reportes diarios)")
    try:
        mac = obtener_macro(dias=7, n=4)
        mac_txt = texto_macro(mac, semanal=True)
        if "titulares macro" in (mac.get("fallos") or []):
            avisos.append("macro sin titulares (solo mercados)")
    except Exception as e:
        print("Macro semanal no disponible:", e)
        mac_txt = ""
        avisos.append("macro")

    hoy = next((d for d in semana if d.get("fecha") == fecha_iso(velas[-1])), None)
    return texto_semanal(velas, est, fg_hist[-7:], noticias_sem, pos,
                         texto_expectativas(exp, sesgo_expectativas(exp)), escenarios_sem,
                         texto_ciclo(ciclo), mac_txt, noticias_prom=noticias_prom,
                         veredicto=veredicto_semanal(hoy, ciclo, est, semana),
                         marcador=texto_marcador(hist, velas), avisos=avisos)


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
    avisos = []
    try:
        fg_hist = fear_and_greed(10)
    except Exception as e:
        # Si alternative.me no responde, el bot NO se cae: usa los últimos valores de la memoria
        print("Fear & Greed no disponible:", e)
        fg_hist = [(d["fear_greed"], "último dato guardado") for d in hist if d.get("fear_greed") is not None][-10:]
        if not fg_hist:
            raise
        avisos.append("Fear &amp; Greed (uso el último valor guardado)")
    pos = obtener_posicionamiento()
    sesgo_pos = sesgo_multitud(pos)

    domingo_cerrado = datetime.fromtimestamp(int(velas[-1][0]), tz=timezone.utc).weekday() == 6

    if modo in ("auto", "diario"):
        r = enviar_telegram(reporte_diario(velas, est, fg_hist, pos, sesgo_pos, hist, avisos))
        print("Diario:", "OK" if r.get("ok") else r)
    if modo == "semanal" or (modo == "auto" and domingo_cerrado):
        r = enviar_telegram(reporte_semanal(velas, est, fg_hist, pos))
        print("Semanal:", "OK" if r.get("ok") else r)


if __name__ == "__main__":
    main()
