"""
Routes de l'interface de démo prospect.

Sert la page HTML et gère les uploads CSV/Excel pour la flotte et les commandes.
"""

from __future__ import annotations

import csv
import io
import uuid
from pathlib import Path
from typing import Any

import openpyxl
from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import StreamingResponse
from fastapi.templating import Jinja2Templates

from app.api.schemas import AssignedOrderSchema, CourierResponse, DispatchResponse, OrderResponse
from app.models.courier import Courier, GpsPosition
from app.models.enums import VehicleType, Zone, VolumeType
from app.models.order import Coordinates, Order
from app.services.dispatch import dispatch_order
from app.services.fleet import fleet_manager

router = APIRouter(tags=["Démo"])

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# ---------------------------------------------------------------------------
# Page principale
# ---------------------------------------------------------------------------

@router.get("/", include_in_schema=False)
def index(request: Request):
    """Sert la page HTML de démonstration."""
    return templates.TemplateResponse("index.html", {"request": request})


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------

@router.post("/demo/reset", tags=["Démo"])
def reset_demo() -> dict:
    """Remet à zéro toute la flotte et toutes les commandes."""
    fleet_manager.reset()
    return {"status": "ok", "message": "Flotte et commandes réinitialisées."}


# ---------------------------------------------------------------------------
# Téléchargement des modèles CSV
# ---------------------------------------------------------------------------

FLEET_TEMPLATE_CSV = (
    "code,vehicle_type,lat,lon\n"
    "KEN,scoot_ville,48.8566,2.3522\n"
    "THO,scoot_ville,48.8864,2.3432\n"
    "ALI,scoot_ville,48.8533,2.3692\n"
    "MAR,scoot_banlieue_proche,48.9360,2.3553\n"
    "LEA,scoot_banlieue_proche,48.8948,2.3833\n"
    "SAM,scoot_banlieue_loin,48.9906,2.3797\n"
    "FOU,fourgon,48.8045,2.1200\n"
    "MAX,fourgon,48.7773,2.4555\n"
)

ORDERS_TEMPLATE_CSV = (
    "id,pickup_lat,pickup_lon,delivery_lat,delivery_lon,zone,volume_type\n"
    "ORD-001,48.8559,2.3578,48.8864,2.3432,Paris,Standard\n"
    "ORD-002,48.8533,2.3692,48.8948,2.3833,Paris,Volume\n"
    "ORD-003,48.9360,2.3553,48.9500,2.3700,Petite_Couronne,Standard\n"
    "ORD-004,48.8045,2.1200,48.7700,2.0800,Grande_Couronne,Voiture\n"
)


@router.get("/demo/templates/fleet", include_in_schema=False)
def download_fleet_template():
    """Télécharge le modèle CSV pour importer une flotte."""
    return StreamingResponse(
        io.StringIO(FLEET_TEMPLATE_CSV),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=modele_flotte.csv"},
    )


@router.get("/demo/templates/orders", include_in_schema=False)
def download_orders_template():
    """Télécharge le modèle CSV pour importer des commandes."""
    return StreamingResponse(
        io.StringIO(ORDERS_TEMPLATE_CSV),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=modele_commandes.csv"},
    )


# ---------------------------------------------------------------------------
# Parsing des fichiers CSV / Excel
# ---------------------------------------------------------------------------

def _parse_file(content: bytes, filename: str) -> list[dict[str, Any]]:
    """
    Détecte le format (CSV ou Excel) et retourne une liste de dicts (une entrée par ligne).
    Gère le BOM UTF-8 et les délimiteurs virgule/point-virgule.
    """
    fname = filename.lower()

    if fname.endswith((".xlsx", ".xls")):
        wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return []
        headers = [str(h).strip() if h is not None else "" for h in rows[0]]
        result = []
        for row in rows[1:]:
            if not any(v is not None for v in row):
                continue  # ignore les lignes vides
            result.append({headers[i]: (str(v).strip() if v is not None else "") for i, v in enumerate(row)})
        return result

    # CSV (utf-8-sig gère le BOM automatiquement)
    text = content.decode("utf-8-sig", errors="replace")
    # Auto-détection du délimiteur (virgule ou point-virgule)
    first_line = text.split("\n")[0]
    delimiter = ";" if first_line.count(";") > first_line.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    return [
        {k.strip(): v.strip() for k, v in row.items()}
        for row in reader
        if any(v.strip() for v in row.values())
    ]


# ---------------------------------------------------------------------------
# Upload flotte
# ---------------------------------------------------------------------------

@router.post("/demo/fleet/upload")
async def upload_fleet(file: UploadFile = File(...)) -> dict:
    """
    Importe une flotte depuis un fichier CSV ou Excel.

    Colonnes attendues : code, vehicle_type, lat, lon
    Retourne un résumé : ajoutés, ignorés (doublons), erreurs de format.
    """
    content = await file.read()
    rows = _parse_file(content, file.filename or "upload")

    added: list[dict] = []
    skipped: list[dict] = []
    errors: list[dict] = []

    for i, row in enumerate(rows, start=2):  # start=2 car ligne 1 = headers
        code = row.get("code", "").upper().strip()
        vehicle_type_raw = row.get("vehicle_type", "").strip()
        lat_raw = row.get("lat", "")
        lon_raw = row.get("lon", "")

        # Validation
        try:
            if not code or len(code) != 3:
                raise ValueError(f"Code invalide : '{code}' (doit faire 3 lettres)")
            vehicle_type = VehicleType(vehicle_type_raw)
            lat = float(lat_raw)
            lon = float(lon_raw)
        except ValueError as e:
            errors.append({"row": i, "code": code, "reason": str(e)})
            continue

        # Ajout dans la flotte
        try:
            courier = Courier(
                code=code,
                vehicle_type=vehicle_type,
                position=GpsPosition(lat=lat, lon=lon),
            )
            fleet_manager.add_courier(courier)
            added.append({"code": code, "vehicle_type": vehicle_type, "lat": lat, "lon": lon})
        except ValueError:
            skipped.append({"code": code, "reason": "Code déjà enregistré"})

    return {
        "added": len(added),
        "skipped": len(skipped),
        "errors": len(errors),
        "details": {"added": added, "skipped": skipped, "errors": errors},
    }


# ---------------------------------------------------------------------------
# Upload commandes
# ---------------------------------------------------------------------------

@router.post("/demo/orders/upload")
async def upload_orders(file: UploadFile = File(...)) -> dict:
    """
    Importe et dispatche des commandes depuis un fichier CSV ou Excel.

    Colonnes attendues : id, pickup_lat, pickup_lon, delivery_lat, delivery_lon, zone, volume_type
    Chaque commande est immédiatement soumise au moteur de dispatch.
    """
    content = await file.read()
    rows = _parse_file(content, file.filename or "upload")

    results: list[dict] = []
    parse_errors: list[dict] = []

    for i, row in enumerate(rows, start=2):
        order_id = row.get("id", "").strip() or f"UP-{uuid.uuid4().hex[:6].upper()}"
        zone_raw = row.get("zone", "").strip()
        vol_raw = row.get("volume_type", "").strip()

        try:
            zone = Zone(zone_raw)
            volume_type = VolumeType(vol_raw)
            pickup_lat = float(row.get("pickup_lat", 0))
            pickup_lon = float(row.get("pickup_lon", 0))
            delivery_lat = float(row.get("delivery_lat", 0))
            delivery_lon = float(row.get("delivery_lon", 0))
        except (ValueError, KeyError) as e:
            parse_errors.append({"row": i, "id": order_id, "reason": str(e)})
            continue

        # Évite les doublons d'ID
        if fleet_manager.get_order(order_id):
            order_id = f"{order_id}-{uuid.uuid4().hex[:4].upper()}"

        order = Order(
            id=order_id,
            pickup=Coordinates(lat=pickup_lat, lon=pickup_lon),
            delivery=Coordinates(lat=delivery_lat, lon=delivery_lon),
            zone=zone,
            volume_type=volume_type,
        )
        fleet_manager.add_order(order)
        result = dispatch_order(order, fleet_manager)

        results.append({
            "order_id": result.order_id,
            "zone": zone,
            "volume_type": volume_type,
            "success": result.success,
            "assigned_to": result.assigned_to,
            "score": result.score,
            "reason": result.reason,
            "pickup_lat": pickup_lat,
            "pickup_lon": pickup_lon,
            "delivery_lat": delivery_lat,
            "delivery_lon": delivery_lon,
        })

    assigned = sum(1 for r in results if r["success"])
    return {
        "total": len(results),
        "assigned": assigned,
        "unassignable": len(results) - assigned,
        "parse_errors": len(parse_errors),
        "results": results,
        "errors": parse_errors,
    }
