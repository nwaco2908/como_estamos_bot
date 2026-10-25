"""
Como Estamos - Posicionamiento multi-exchange: ¿la gente está en long o en short?

Combina varios exchanges para no depender de uno solo (cada uno tiene su propio tipo de trader):
- Ratio long/short de cuentas: Bitget, Binance, OKX, Bybit
- Funding rate: Bitget, Binance, OKX, Bybit, Hyperliquid
- Interés abierto: Bitget

Cada ratio long/short se compara contra SU PROPIO promedio de ~30 días, porque cada exchange
tiene un nivel "normal" distinto (en Bitget la gente casi siempre está más long que en Binance).

Lectura contrarian: cuando la mayoría está de un lado y el precio no le da la razón,
ese lado se convierte en combustible para el movimiento contrario (short/long squeeze).
"""

import json
import urllib.request


def _get(url, metodo="GET", cuerpo=None):
    datos = json.dumps(cuerpo).encode("utf-8") if cuerpo is not None else None
    req = urllib.request.Request(url, data=datos, method=metodo,
                                 headers={"User-Agent": "como-estamos-bot", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _num(d, *claves):
    for c in claves:
        if isinstance(d, dict) and d.get(c) not in (None, ""):
            return float(d[c])
    return None


def _ratios_de_filas(filas):
    """Acepta filas como diccionarios o listas [ts, ratio]; devuelve ratios en orden temporal."""
    pares = []
    for f in filas or []:
        if isinstance(f, (list, tuple)) and len(f) >= 2:
            ts, r = int(f[0]), float(f[1])
        elif isinstance(f, dict):
            ts = int(f.get("ts") or f.get("timestamp") or 0)
            r = _num(f, "longShortAccountRatio", "longShortRatio", "ratio")
            if r is None:
                lg = _num(f, "longAccountRatio", "longRatio", "longAccount", "buyRatio")
                sh = _num(f, "shortAccountRatio", "shortRatio", "shortAccount", "sellRatio")
                r = lg / sh if lg and sh else None
        else:
            continue
        if r:
            pares.append((ts, r))
    pares.sort()
    return [r for _, r in pares]


# ───────────────────────── RATIO LONG/SHORT POR EXCHANGE ─────────────────────────
def _ls_bitget():
    base = "https://api.bitget.com/api/v2/mix/market/account-long-short?symbol=BTCUSDT"
    for extra in ("&productType=usdt-futures&period=1d", "&period=1d", "&period=1D", "&period=4h"):
        try:
            d = _get(base + extra)
            if d.get("code") == "00000":
                r = _ratios_de_filas(d.get("data"))
                if r:
                    return r
        except Exception:
            continue
    raise RuntimeError("sin datos")


def _ls_binance():
    d = _get("https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol=BTCUSDT&period=1d&limit=30")
    return _ratios_de_filas(d)


def _ls_okx():
    d = _get("https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio?ccy=BTC&period=1D")
    if d.get("code") != "0":
        raise RuntimeError(d.get("msg"))
    return _ratios_de_filas(d.get("data"))


def _ls_bybit():
    d = _get("https://api.bybit.com/v5/market/account-ratio?category=linear&symbol=BTCUSDT&period=1d&limit=30")
    if d.get("retCode") != 0:
        raise RuntimeError(d.get("retMsg"))
    return _ratios_de_filas(d.get("result", {}).get("list"))


# ───────────────────────── FUNDING POR EXCHANGE (tasa cada 8 h) ─────────────────────────
def _fu_bitget():
    d = _get("https://api.bitget.com/api/v2/mix/market/current-fund-rate?symbol=BTCUSDT&productType=usdt-futures")
    fila = d["data"][0] if isinstance(d.get("data"), list) else d.get("data")
    return _num(fila, "fundingRate")


def _fu_binance():
    return _num(_get("https://fapi.binance.com/fapi/v1/premiumIndex?symbol=BTCUSDT"), "lastFundingRate")


def _fu_okx():
    d = _get("https://www.okx.com/api/v5/public/funding-rate?instId=BTC-USDT-SWAP")
    return _num(d["data"][0], "fundingRate")


def _fu_bybit():
    d = _get("https://api.bybit.com/v5/market/tickers?category=linear&symbol=BTCUSDT")
    return _num(d["result"]["list"][0], "fundingRate")


def _fu_hyperliquid():
    meta, ctxs = _get("https://api.hyperliquid.xyz/info", "POST", {"type": "metaAndAssetCtxs"})
    for activo, ctx in zip(meta["universe"], ctxs):
        if activo.get("name") == "BTC":
            return float(ctx["funding"]) * 8  # Hyperliquid cobra cada hora; lo llevamos a 8 h
    return None


def _funding_hist_bitget():
    d = _get("https://api.bitget.com/api/v2/mix/market/history-fund-rate?symbol=BTCUSDT&productType=usdt-futures&pageSize=21")
    return [float(h["fundingRate"]) for h in d.get("data") or [] if h.get("fundingRate") not in (None, "")]


def _oi_bitget():
    d = _get("https://api.bitget.com/api/v2/mix/market/open-interest?symbol=BTCUSDT&productType=usdt-futures")
    datos = d.get("data")
    lista = datos.get("openInterestList") if isinstance(datos, dict) else None
    return _num(lista[0], "size", "amount") if lista else _num(datos, "amount", "size")


EXCHANGES_LS = {"Bitget": _ls_bitget, "Binance": _ls_binance, "OKX": _ls_okx, "Bybit": _ls_bybit}
EXCHANGES_FU = {"Bitget": _fu_bitget, "Binance": _fu_binance, "OKX": _fu_okx,
                "Bybit": _fu_bybit, "Hyperliquid": _fu_hyperliquid}


def _resumen_ratios(ratios):
    ratios = ratios[-30:]
    actual = ratios[-1]
    promedio = sum(ratios) / len(ratios)
    return {"ratio": actual, "promedio": promedio, "pct_long": actual / (1 + actual) * 100,
            "dias": len(ratios), "hace_7d": ratios[-8] if len(ratios) >= 8 else None,
            "desvio": (actual / promedio - 1) * 100}


def obtener_posicionamiento():
    ls_por_ex, fu_por_ex, fallos = {}, {}, []

    for nombre, funcion in EXCHANGES_LS.items():
        try:
            r = funcion()
            if r:
                ls_por_ex[nombre] = _resumen_ratios(r)
        except Exception as e:
            print(f"Long/short {nombre} no disponible:", e)
    for nombre, funcion in EXCHANGES_FU.items():
        try:
            f = funcion()
            if f is not None:
                fu_por_ex[nombre] = f
        except Exception as e:
            print(f"Funding {nombre} no disponible:", e)

    # "long_short" = promedio de los exchanges que respondieron (lo usan el resumen semanal y el historial)
    long_short = None
    if ls_por_ex:
        vals = list(ls_por_ex.values())
        con_7d = [v for v in vals if v["hace_7d"]]
        long_short = {
            "ratio": sum(v["ratio"] for v in vals) / len(vals),
            "promedio": sum(v["promedio"] for v in vals) / len(vals),
            "pct_long": sum(v["pct_long"] for v in vals) / len(vals),
            "dias": min(v["dias"] for v in vals),
            "hace_7d": (sum(v["hace_7d"] for v in con_7d) / len(con_7d)) if len(con_7d) == len(vals) else None,
            "desvio": sum(v["desvio"] for v in vals) / len(vals),
            "fuentes": list(ls_por_ex),
        }
    else:
        fallos.append("long/short")

    funding = None
    if fu_por_ex:
        hist = []
        try:
            hist = _funding_hist_bitget()
        except Exception as e:
            print("Funding histórico no disponible:", e)
        funding = {"actual": sum(fu_por_ex.values()) / len(fu_por_ex),
                   "promedio_7d": sum(hist) / len(hist) if hist else None,
                   "fuentes": list(fu_por_ex)}
    else:
        fallos.append("funding")

    oi = None
    try:
        oi = _oi_bitget()
    except Exception as e:
        print("Interés abierto no disponible:", e)

    return {"long_short": long_short, "funding": funding, "oi": oi,
            "por_exchange": {"ls": ls_por_ex, "funding": fu_por_ex}, "fallos": fallos}


def sesgo_multitud(pos):
    """
    -100 = multitud muy en short; +100 = multitud muy en long.
    Promedia el funding (en % cada 8 h) y cuánto se desvía cada exchange de su ratio normal.
    """
    partes = []
    f = pos.get("funding") or {}
    if f.get("actual") is not None:
        pct = f["actual"] * 100  # 0.01 % cada 8 h es el valor "normal"
        partes.append(max(-100, min(100, (pct - 0.01) / 0.04 * 100)))
    ls = pos.get("long_short") or {}
    if ls.get("desvio") is not None:
        partes.append(max(-100, min(100, ls["desvio"] * 4)))
    if not partes:
        return None
    return round(sum(partes) / len(partes))


def texto_posicionamiento(pos, sesgo):
    detalle = pos.get("por_exchange") or {}
    fuentes = sorted(set(detalle.get("ls") or {}) | set(detalle.get("funding") or {}))
    lineas = [f"⚖️ <b>Posicionamiento del mercado</b> (promedio de {len(fuentes)} exchanges)"]

    ls = pos.get("long_short")
    if ls:
        d = ls["desvio"]
        comparado = ("más long de lo normal" if d > 5 else "más short de lo normal" if d < -5
                     else "en su nivel normal")
        lineas.append(f"Cuentas: {ls['pct_long']:.0f}% long / {100 - ls['pct_long']:.0f}% short → {comparado} ({d:+.0f}%)")

    f = pos.get("funding")
    if f and f.get("actual") is not None:
        texto = f"Funding: {f['actual'] * 100:+.4f}%"
        if f["actual"] < 0:
            texto += " → los shorts pagan"
        elif f["actual"] * 100 > 0.03:
            texto += " → longs sobrecargados"
        else:
            texto += " → normal"
        lineas.append(texto)
    if pos.get("oi"):
        lineas.append(f"Interés abierto (Bitget): {pos['oi']:,.0f} BTC")
    if sesgo is not None:
        if sesgo <= -40:
            lectura = "🐻 Multitud cargada en SHORT"
        elif sesgo >= 40:
            lectura = "🐂 Multitud cargada en LONG"
        else:
            lectura = "Posicionamiento equilibrado"
        lineas.append(f"Sesgo de la multitud: <b>{sesgo:+d}</b> {lectura}")
    if fuentes:
        lineas.append(f"<i>{', '.join(fuentes)}</i>")
    if pos.get("fallos"):
        lineas.append(f"<i>Sin datos: {', '.join(pos['fallos'])}</i>")
    return "\n".join(lineas)
