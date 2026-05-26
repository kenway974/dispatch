"""
Endpoints FastAPI du moteur de dispatch.

Routes disponibles :
  GET  /health                        → état général du système
  POST /orders                        → soumettre une commande (déclenche le dispatch)
  GET  /orders/{order_id}             → détail d'une commande
  GET  /orders                        → liste toutes les commandes
  POST /couriers                      → enregistrer un nouveau coursier
  GET  /couriers                      → liste tous les coursiers
  GET  /couriers/{code}               → détail d'un coursier
  PUT  /couriers/{code}/position      → mettre à jour la position GPS
  PUT  /couriers/{code}/active        → activer / désactiver un coursier
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.api.schemas import (
    CreateCoursierRequest,
    CreateOrderRequest,
    CoursierResponse,
    DispatchResponse,
    HealthResponse,
    OrderResponse,
    UpdatePositionRequest,
    UpdateCoursierRequest,
    AssignedOrderSchema,
)
from app.models.coursier import Coursier, GpsPosition
from app.models.order import Order, Coordinates
from app.services.dispatch import dispatch_order
from app.services.fleet import fleet_manager

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers de conversion modèle → schéma de réponse
# ---------------------------------------------------------------------------

def _coursier_to_response(coursier: Coursier) -> CoursierResponse:
    """Sérialise un Coursier interne en CoursierResponse."""
    return CoursierResponse(
        code=coursier.code,
        vehicle_type=coursier.vehicle_type,
        lat=coursier.position.lat,
        lon=coursier.position.lon,
        is_active=coursier.is_active,
        current_load=coursier.current_load,
        max_load=coursier.max_load,
        remaining_capacity=coursier.remaining_capacity,
        order_count=coursier.order_count,
        assigned_orders=[
            AssignedOrderSchema(
                order_id=o.order_id,
                pickup_lat=o.pickup_lat,
                pickup_lon=o.pickup_lon,
                delivery_lat=o.delivery_lat,
                delivery_lon=o.delivery_lon,
                volume_type=o.volume_type,
                weight=o.weight,
            )
            for o in coursier.assigned_orders
        ],
    )


def _order_to_response(order: Order) -> OrderResponse:
    """Sérialise un Order interne en OrderResponse."""
    return OrderResponse(
        id=order.id,
        pickup_lat=order.pickup.lat,
        pickup_lon=order.pickup.lon,
        delivery_lat=order.delivery.lat,
        delivery_lon=order.delivery.lon,
        zone=order.zone,
        volume_type=order.volume_type,
        client_tier=order.client_tier,
        deadline_minutes=order.deadline_minutes,
        status=order.status,
        assigned_coursier=order.assigned_coursier,
        created_at=order.created_at,
    )


# ---------------------------------------------------------------------------
# Endpoints système
# ---------------------------------------------------------------------------

@router.get("/health", response_model=HealthResponse, tags=["Système"])
def health_check() -> HealthResponse:
    """Retourne l'état général du moteur de dispatch."""
    return HealthResponse(
        status="ok",
        coursier_count=fleet_manager.coursier_count,
        order_count=fleet_manager.order_count,
        coursiers_actifs=len(fleet_manager.get_active_coursiers()),
    )


# ---------------------------------------------------------------------------
# Endpoints commandes
# ---------------------------------------------------------------------------

@router.post(
    "/orders",
    response_model=DispatchResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Commandes"],
    summary="Soumettre une commande et déclencher le dispatch automatique",
)
def create_order(payload: CreateOrderRequest) -> DispatchResponse:
    """
    Reçoit une nouvelle commande, l'enregistre et lance immédiatement
    le moteur d'attribution pour trouver le meilleur coursier disponible.
    """
    # Vérifie que l'ID est unique
    if fleet_manager.get_order(payload.id) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Une commande avec l'ID '{payload.id}' existe déjà.",
        )

    # Construit l'objet Order
    order = Order(
        id=payload.id,
        pickup=Coordinates(lat=payload.pickup_lat, lon=payload.pickup_lon),
        delivery=Coordinates(lat=payload.delivery_lat, lon=payload.delivery_lon),
        zone=payload.zone,
        volume_type=payload.volume_type,
        client_tier=payload.client_tier,
        deadline_minutes=payload.deadline_minutes,
    )

    # Enregistre la commande dans le store
    fleet_manager.add_order(order)

    # Lance le dispatch
    result = dispatch_order(order, fleet_manager)

    return DispatchResponse(
        success=result.success,
        order_id=result.order_id,
        assigned_to=result.assigned_to,
        score=result.score,
        reason=result.reason,
        eligible_count=result.eligible_count,
        order=_order_to_response(order),
    )


@router.get("/orders", response_model=list[OrderResponse], tags=["Commandes"])
def list_orders() -> list[OrderResponse]:
    """Retourne toutes les commandes enregistrées dans le système."""
    return [_order_to_response(o) for o in fleet_manager.list_orders()]


@router.get("/orders/{order_id}", response_model=OrderResponse, tags=["Commandes"])
def get_order(order_id: str) -> OrderResponse:
    """Retourne le détail et le statut courant d'une commande."""
    order = fleet_manager.get_order(order_id)
    if order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Commande '{order_id}' introuvable.",
        )
    return _order_to_response(order)


# ---------------------------------------------------------------------------
# Endpoints coursiers
# ---------------------------------------------------------------------------

@router.post(
    "/coursiers",
    response_model=CoursierResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Coursiers"],
    summary="Enregistrer un nouveau coursier dans la flotte",
)
def create_coursier(payload: CreateCoursierRequest) -> CoursierResponse:
    """Ajoute un coursier à la flotte avec sa position GPS initiale."""
    try:
        coursier = Coursier(
            code=payload.code,
            vehicle_type=payload.vehicle_type,
            position=GpsPosition(lat=payload.lat, lon=payload.lon),
        )
        fleet_manager.add_coursier(coursier)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    return _coursier_to_response(coursier)


@router.get("/coursiers", response_model=list[CoursierResponse], tags=["Coursiers"])
def list_coursiers() -> list[CoursierResponse]:
    """Retourne l'ensemble des coursiers avec leur état temps réel."""
    return [_coursier_to_response(c) for c in fleet_manager.list_coursiers()]


@router.get("/coursiers/{code}", response_model=CoursierResponse, tags=["Coursiers"])
def get_coursier(code: str) -> CoursierResponse:
    """Retourne le détail d'un coursier et ses courses en cours."""
    coursier = fleet_manager.get_coursier(code)
    if coursier is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Coursier '{code.upper()}' introuvable.",
        )
    return _coursier_to_response(coursier)


@router.put(
    "/coursiers/{code}/position",
    response_model=CoursierResponse,
    tags=["Coursiers"],
    summary="Mettre à jour la position GPS d'un coursier en temps réel",
)
def update_position(code: str, payload: UpdatePositionRequest) -> CoursierResponse:
    """Met à jour la position GPS d'un coursier (appelé par l'app mobile du coursier)."""
    try:
        coursier = fleet_manager.update_coursier_position(code, payload.lat, payload.lon)
    except KeyError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    return _coursier_to_response(coursier)


@router.put(
    "/coursiers/{code}/active",
    response_model=CoursierResponse,
    tags=["Coursiers"],
    summary="Activer ou désactiver un coursier",
)
def set_coursier_active(code: str, active: bool) -> CoursierResponse:
    """
    Active ou désactive un coursier (ex: fin de service, panne, pause).
    Un coursier inactif ne reçoit plus aucune attribution.
    """
    coursier = fleet_manager.get_coursier(code)
    if coursier is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Coursier '{code.upper()}' introuvable.",
        )
    fleet_manager.set_coursier_active(code, active)
    return _coursier_to_response(coursier)


@router.patch(
    "/coursiers/{code}",
    response_model=CoursierResponse,
    tags=["Coursiers"],
    summary="Modifier un coursier (véhicule, position, statut)",
)
async def update_coursier(code: str, payload: UpdateCoursierRequest) -> CoursierResponse:
    """
    Mise à jour partielle d'un coursier.
    Seuls les champs fournis (non-None) sont modifiés.

    - vehicle_type : change le type de véhicule (et donc la capacité max et les zones)
    - adresse      : géocode l'adresse via Nominatim et met à jour la position
    - lat + lon    : met à jour la position GPS directement
    - is_active    : active ou désactive le coursier
    """
    from app.api.routes_ui import geocode_address  # import local pour éviter la circularité

    coursier = fleet_manager.get_coursier(code)
    if coursier is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Coursier '{code.upper()}' introuvable.",
        )

    # Géocodage de l'adresse si fournie
    lat, lon = payload.lat, payload.lon
    if payload.adresse:
        try:
            lat, lon = await geocode_address(payload.adresse)
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))

    fleet_manager.update_coursier(
        code,
        vehicle_type=payload.vehicle_type,
        lat=lat,
        lon=lon,
        is_active=payload.is_active,
    )
    return _coursier_to_response(coursier)
