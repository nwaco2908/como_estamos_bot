"""
Como Estamos - Memoria del bot.

Guarda un registro por día en historial.json (dentro del repositorio de GitHub)
para poder comparar: "¿hubo pánico hace 3 días y el precio dejó de caer?"
"""

import json
import os

ARCHIVO = "historial.json"
MAX_DIAS = 180


def cargar(ruta=ARCHIVO):
    if not os.path.exists(ruta):
        return []
    try:
        with open(ruta, encoding="utf-8") as f:
            datos = json.load(f)
        return datos if isinstance(datos, list) else []
    except Exception as e:
        print("Historial ilegible, empiezo de cero:", e)
        return []


def guardar(registro, ruta=ARCHIVO):
    """Agrega (o reemplaza, si ya existe esa fecha) el registro del día."""
    datos = [d for d in cargar(ruta) if d.get("fecha") != registro["fecha"]]
    datos.append(registro)
    datos.sort(key=lambda d: d["fecha"])
    datos = datos[-MAX_DIAS:]
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=1)
    return datos
