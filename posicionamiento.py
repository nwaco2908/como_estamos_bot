"""
Como Estamos - Módulo de posicionamiento (Fase 4b): ¿la gente está en long o en short?

Datos públicos de Bitget (BTCUSDT perpetuo):
- Proporción de cuentas long/short: cuántos traders están comprados vs. vendidos.
- Funding rate: positivo = los longs pagan (exceso de compradores);
                negativo = los shorts pagan (exceso de vendedores).
- Interés abierto (OI): dinero total en posiciones abiertas.

Lectura contrarian: cuando la mayoría está de un lado y el precio no le da la razón,
ese lado se convierte en combustible para el movimiento contrario (short/long squeeze).
"""

import json
import urllib.request

BASE = "https://api.bitget.com/api/v2/mix/market/"
SIMBOLO = "symbol=BTCUSDT&productType=usdt-futures"


def _get(ruta):
    req = urllib.request.Request(BASE + ruta, headers={"User-Agent": "como-estamos-bot"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if data.get("code") != "00000":
        raise RuntimeError(data.get("msg", data))
    return data.get("data")


def _num(d, *claves):
    for c in claves:
        if isinstance(d, dict) and d.get(c) not in (None, ""):
            return float(d[c])
    return None


def _ratios_de_filas(filas):
    """Acepta filas como diccionarios o como listas [ts, ratio] y devuelve ratios en orden temporal."""
    pares = []
    for f in filas:
        if isinstance(f, (list, tuple)) and len(f) >= 2:
            ts, r = int(f[0]), float(f[1])
        elif isinstance(f, dict):
            ts = int(f.get("ts") or f.get("timestamp") or 0)
            r = _num(f, "longShortAccountRatio", "longShortRatio", "ratio")
            if r is None:
                lg = _num(f, "longAccountRatio", "longRatio")
                sh = _num(f, "shortAccountRatio", "shortRatio")
                r = lg / sh if lg and sh else None
        else:
            continue
        if r:
            pares.append((ts, r))
    pares.sort()
    return [r for _, r in pares]


def _ls_bitget():
    ultimo_error = None
    for consulta in (
        f"account-long-short?{SIMBOLO}&period=1d",
        "account-long-short?symbol=BTCUSDT&period=1d",
        "account-long-short?symbol=BTCUSDT&period=1D",
        "account-long-short?symbol=BTCUSDT&period=4h",
    ):
        try:
            ratios = _ratios_de_filas(_get(consulta) or [])
            if ratios:
                return ratios
        except Exception as e:
            ultimo_error = e
    raise RuntimeError(f"Bitget: {ultimo_error}")


def _ls_okx():
    url = "https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio?ccy=BTC&period=1D"
    req = urllib.request.Request(url, headers={"User-Agent": "como-estamos-bot"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if data.get("code") != "0":
        raise RuntimeError(f"OKX: {data.get('msg', data)}")
    return _ratios_de_filas(data.get("data") or [])


def long_short_cuentas():
    """Ratio long/short de cuentas (Bitget; si falla, OKX), actual y promedio de los últimos días."""
    ratios, fuente, errores = [], None, []
    for nombre, funcion in (("Bitget", _ls_bitget), ("OKX", _ls_okx)):
        try:
            ratios = funcion()
            if ratios:
                fuente = nombre
                break
        except Exception as e:
            errores.append(str(e))
    if not ratios:
        raise RuntimeError(" | ".join(errores) or "sin datos")
    ratios = ratios[-30:]
    actual = ratios[-1]
    promedio = sum(ratios) / len(ratios)
    pct_long = actual / (1 + actual) * 100
    hace_7d = ratios[-8] if len(ratios) >= 8 else None
    return {"ratio": actual, "promedio": promedio, "pct_long": pct_long, "dias": len(ratios),
            "hace_7d": hace_7d, "fuente": fuente}


def funding():
    """Funding actual y promedio de los últimos ~7 días (21 pagos de 8 h)."""
    actual = None
    datos = _get(f"current-fund-rate?{SIMBOLO}")
    fila = datos[0] if isinstance(datos, list) and datos else datos
    actual = _num(fila, "fundingRate")
    historial = []
    try:
        hist = _get(f"history-fund-rate?{SIMBOLO}&pageSize=21")
        historial = [float(h["fundingRate"]) for h in hist if h.get("fundingRate") not in (None, "")]
    except Exception as e:
        print("Funding histórico no disponible:", e)
    promedio = sum(historial) / len(historial) if historial else None
    return {"actual": actual, "promedio_7d": promedio}


def interes_abierto():
    datos = _get(f"open-interest?{SIMBOLO}")
    lista = datos.get("openInterestList") if isinstance(datos, dict) else None
    if lista:
        return _num(lista[0], "size", "amount")
    return _num(datos, "amount", "size")


def obtener_posicionamiento():
    resultado, fallos = {}, []
    for nombre, funcion in (("long_short", long_short_cuentas), ("funding", funding), ("oi", interes_abierto)):
        try:
            resultado[nombre] = funcion()
        except Exception as e:
            print(f"Posicionamiento {nombre} no disponible:", e)
            resultado[nombre] = None
            fallos.append(nombre)
    resultado["fallos"] = fallos
    return resultado


def sesgo_multitud(pos):
    """
    Puntaje de -100 (multitud muy en short) a +100 (multitud muy en long).
    Usa el funding (en % cada 8 h) y el ratio long/short frente a su propio promedio.
    """
    partes = []
    f = pos.get("funding") or {}
    if f.get("actual") is not None:
        pct = f["actual"] * 100  # 0.01 = 0.01 % cada 8 h, el valor "normal"
        # 0.01 % es neutral; +0.05 % = muy cargado en long; -0.03 % = muy cargado en short
        partes.append(max(-100, min(100, (pct - 0.01) / 0.04 * 100)))
    ls = pos.get("long_short") or {}
    if ls.get("ratio") and ls.get("promedio"):
        desvio = (ls["ratio"] / ls["promedio"] - 1) * 100  # % por encima o debajo de lo normal
        partes.append(max(-100, min(100, desvio * 4)))
    if not partes:
        return None
    return round(sum(partes) / len(partes))


def texto_posicionamiento(pos, sesgo):
    lineas = ["⚖️ <b>Posicionamiento (Bitget)</b>"]
    ls = pos.get("long_short")
    if ls:
        comparado = "más" if ls["ratio"] > ls["promedio"] else "menos"
        lineas.append(
            f"Cuentas: {ls['pct_long']:.0f}% long / {100 - ls['pct_long']:.0f}% short "
            f"(ratio {ls['ratio']:.2f}, {comparado} long que su promedio de {ls['dias']} días: {ls['promedio']:.2f})"
            + (f" [{ls['fuente']}]" if ls.get("fuente") == "OKX" else "")
        )
    f = pos.get("funding")
    if f and f.get("actual") is not None:
        texto = f"Funding: {f['actual'] * 100:+.4f}%"
        if f.get("promedio_7d") is not None:
            texto += f" (promedio 7 días {f['promedio_7d'] * 100:+.4f}%)"
        if f["actual"] < 0:
            texto += " → los shorts pagan"
        elif f["actual"] * 100 > 0.03:
            texto += " → longs sobrecargados"
        lineas.append(texto)
    if pos.get("oi"):
        lineas.append(f"Interés abierto: {pos['oi']:,.0f} BTC")
    if sesgo is not None:
        if sesgo <= -40:
            lectura = "🐻 Multitud cargada en SHORT"
        elif sesgo >= 40:
            lectura = "🐂 Multitud cargada en LONG"
        else:
            lectura = "Posicionamiento equilibrado"
        lineas.append(f"Sesgo de la multitud: <b>{sesgo:+d}</b> {lectura}")
    if pos.get("fallos"):
        lineas.append(f"<i>Sin datos: {', '.join(pos['fallos'])}</i>")
    return "\n".join(lineas)
