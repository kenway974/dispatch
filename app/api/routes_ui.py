"""
Routes de l'interface de démo prospect.

Sert la page HTML et gère les uploads CSV/Excel pour la flotte et les commandes.
"""

from __future__ import annotations

import asyncio
import csv
import io
import uuid
from pathlib import Path
from typing import Any, Optional

import httpx
import openpyxl
from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import StreamingResponse
from fastapi.templating import Jinja2Templates

from app.api.schemas import AssignedOrderSchema, CoursierResponse, DispatchResponse, OrderResponse
from app.models.coursier import Coursier, GpsPosition
from app.models.enums import ClientTier, VehicleType, Zone, VolumeType
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
    return templates.TemplateResponse(request, "index.html")


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

# ---------------------------------------------------------------------------
# Géocodage via Nominatim (OpenStreetMap) — sans clé API
# ---------------------------------------------------------------------------

async def geocode_address(address: str) -> tuple[float, float]:
    """
    Convertit une adresse texte en coordonnées GPS (lat, lon) via Nominatim.
    Priorité à la France mais accepte les adresses internationales.

    Raises:
        ValueError: Si l'adresse n'est pas trouvée.
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": address, "format": "json", "limit": 1, "countrycodes": "fr"},
            headers={"User-Agent": "DispatchEngine-Demo/1.0", "Accept-Language": "fr"},
        )
        data = resp.json()
        if not data:
            # Retry sans restriction de pays
            resp2 = await client.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": address, "format": "json", "limit": 1},
                headers={"User-Agent": "DispatchEngine-Demo/1.0"},
            )
            data = resp2.json()
        if not data:
            raise ValueError(f"Adresse introuvable : « {address} »")
        return float(data[0]["lat"]), float(data[0]["lon"])


# ---------------------------------------------------------------------------
# Templates CSV
# ---------------------------------------------------------------------------
FLEET_TEMPLATE_CSV = (
    "code,vehicle_type,adresse\n"
    "KEN,scoot_banlieue_proche,\"Le Marais, Paris\"\n"
    "MEH,scoot_banlieue_proche,\"Place du Tertre, Montmartre, Paris\"\n"
    "LIM,scoot_banlieue_proche,\"Place du 18 Juin 1940, Montparnasse, Paris\"\n"
    "MIC,scoot_banlieue_proche,\"Batignolles, Paris\"\n"
    "MAT,scoot_banlieue_proche,\"Place de la Nation, Paris\"\n"
    "JC,scoot_banlieue_loin,\"Place du 8 mai 1945, Saint-Denis\"\n"
    "MEF,scoot_banlieue_loin,\"Place Salvador Allende, Créteil\"\n"
    "ABD,scoot_banlieue_loin,\"Place de la République, Bondy\"\n"
    "JEA,longue_distance,\"Aéroport d'Orly, Paray-Vieille-Poste\"\n"
    "SET,longue_distance,\"Aéroport Charles de Gaulle, Roissy\"\n"
    "LAH,fourgon,\"Place d'Armes, Versailles\"\n"
    "CAR,fourgon,\"Mairie de Vitry-sur-Seine\"\n"
)

# Modèle commandes : adresses + client_tier + deadline_minutes
ORDERS_TEMPLATE_CSV = (
    "id,adresse_ramassage,adresse_livraison,zone,volume_type,client_tier,deadline_minutes\n"
    "ORD-001,\"12 rue de Rivoli, Paris\",\"Place du Tertre, Montmartre, Paris\",Paris,Standard,standard,45\n"
    "ORD-002,\"Place de la Bastille, Paris\",\"Gare du Nord, Paris\",Paris,Volume,premium,30\n"
    "ORD-003,\"Place du 8 mai 1945, Saint-Denis\",\"Mairie de Pantin\",Petite_Couronne,Standard,standard,\n"
    "ORD-004,\"Place d'Armes, Versailles\",\"Gare de Versailles Chantiers\",Grande_Couronne,Voiture,premium,90\n"
)


@router.get("/demo/templates/fleet", include_in_schema=False)
def download_fleet_template():
    """Télécharge le modèle CSV pour importer une flotte (avec adresses)."""
    return StreamingResponse(
        io.StringIO(FLEET_TEMPLATE_CSV),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=modele_flotte.csv"},
    )


@router.get("/demo/templates/orders", include_in_schema=False)
def download_orders_template():
    """Télécharge le modèle CSV pour importer des commandes (avec adresses)."""
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

    Accepte deux formats :
    - GPS     : code, vehicle_type, lat, lon
    - Adresse : code, vehicle_type, adresse   ← géocodé automatiquement via Nominatim
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
        adresse = row.get("adresse", "").strip()
        lat_raw = row.get("lat", "").strip()
        lon_raw = row.get("lon", "").strip()

        try:
            if not code or len(code) != 3:
                raise ValueError(f"Code invalide : '{code}' (doit faire 3 lettres)")
            vehicle_type = VehicleType(vehicle_type_raw)

            # Résolution des coordonnées : adresse prioritaire sur lat/lon
            if adresse:
                await asyncio.sleep(1.1)  # Nominatim : max 1 requête/seconde
                lat, lon = await geocode_address(adresse)
            else:
                lat = float(lat_raw)
                lon = float(lon_raw)

        except ValueError as e:
            errors.append({"row": i, "code": code, "reason": str(e)})
            continue

        # Ajout dans la flotte
        try:
            coursier = Coursier(
                code=code,
                vehicle_type=vehicle_type,
                position=GpsPosition(lat=lat, lon=lon),
            )
            fleet_manager.add_coursier(coursier)
            added.append({"code": code, "vehicle_type": str(vehicle_type), "lat": lat, "lon": lon})
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

    Accepte deux formats :
    - Adresse : id, adresse_ramassage, adresse_livraison, zone, volume_type  ← géocodé auto
    - GPS     : id, pickup_lat, pickup_lon, delivery_lat, delivery_lon, zone, volume_type
    Chaque commande est immédiatement soumise au moteur de dispatch.
    """
    content = await file.read()
    rows = _parse_file(content, file.filename or "upload")

    results: list[dict] = []
    parse_errors: list[dict] = []

    for i, row in enumerate(rows, start=2):
        order_id = row.get("id", "").strip() or f"UP-{uuid.uuid4().hex[:6].upper()}"
        zone_raw   = row.get("zone", "").strip()
        vol_raw    = row.get("volume_type", "").strip()
        tier_raw   = row.get("client_tier", "standard").strip() or "standard"
        dl_raw     = row.get("deadline_minutes", "").strip()

        # Détection du format : adresse ou coordonnées ?
        pickup_addr   = row.get("adresse_ramassage", "").strip()
        delivery_addr = row.get("adresse_livraison", "").strip()

        try:
            zone        = Zone(zone_raw)
            volume_type = VolumeType(vol_raw)
            client_tier = ClientTier(tier_raw)
            deadline_minutes: Optional[int] = int(dl_raw) if dl_raw.isdigit() else None

            if pickup_addr and delivery_addr:
                # Format adresse → géocodage avec pause pour respecter Nominatim
                await asyncio.sleep(1.1)
                pickup_lat, pickup_lon = await geocode_address(pickup_addr)
                await asyncio.sleep(1.1)
                delivery_lat, delivery_lon = await geocode_address(delivery_addr)
            else:
                # Format GPS direct
                pickup_lat    = float(row.get("pickup_lat", 0))
                pickup_lon    = float(row.get("pickup_lon", 0))
                delivery_lat  = float(row.get("delivery_lat", 0))
                delivery_lon  = float(row.get("delivery_lon", 0))

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
            client_tier=client_tier,
            deadline_minutes=deadline_minutes,
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
