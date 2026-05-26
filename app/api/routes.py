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
    CreateCourierRequest,
    CreateOrderRequest,
    CourierResponse,
    DispatchResponse,
    HealthResponse,
    OrderResponse,
    UpdatePositionRequest,
    AssignedOrderSchema,
)
from app.models.courier import Courier, GpsPosition
from app.models.order import Order, Coordinates
from app.services.dispatch import dispatch_order
from app.services.fleet import fleet_manager

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers de conversion modèle → schéma de réponse
# ---------------------------------------------------------------------------

def _courier_to_response(courier: Courier) -> CourierResponse:
    """Sérialise un Courier interne en CourierResponse."""
    return CourierResponse(
        code=courier.code,
        vehicle_type=courier.vehicle_type,
        lat=courier.position.lat,
        lon=courier.position.lon,
        is_active=courier.is_active,
        current_load=courier.current_load,
        max_load=courier.max_load,
        remaining_capacity=courier.remaining_capacity,
        order_count=courier.order_count,
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
            for o in courier.assigned_orders
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
        assigned_courier=order.assigned_courier,
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
        courier_count=fleet_manager.courier_count,
        order_count=fleet_manager.order_count,
        active_couriers=len(fleet_manager.get_active_couriers()),
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
    "/couriers",
    response_model=CourierResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Coursiers"],
    summary="Enregistrer un nouveau coursier dans la flotte",
)
def create_courier(payload: CreateCourierRequest) -> CourierResponse:
    """Ajoute un coursier à la flotte avec sa position GPS initiale."""
    try:
        courier = Courier(
            code=payload.code,
            vehicle_type=payload.vehicle_type,
            position=GpsPosition(lat=payload.lat, lon=payload.lon),
        )
        fleet_manager.add_courier(courier)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    return _courier_to_response(courier)


@router.get("/couriers", response_model=list[CourierResponse], tags=["Coursiers"])
def list_couriers() -> list[CourierResponse]:
    """Retourne l'ensemble des coursiers avec leur état temps réel."""
    return [_courier_to_response(c) for c in fleet_manager.list_couriers()]


@router.get("/couriers/{code}", response_model=CourierResponse, tags=["Coursiers"])
def get_courier(code: str) -> CourierResponse:
    """Retourne le détail d'un coursier et ses courses en cours."""
    courier = fleet_manager.get_courier(code)
    if courier is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Coursier '{code.upper()}' introuvable.",
        )
    return _courier_to_response(courier)


@router.put(
    "/couriers/{code}/position",
    response_model=CourierResponse,
    tags=["Coursiers"],
    summary="Mettre à jour la position GPS d'un coursier en temps réel",
)
def update_position(code: str, payload: UpdatePositionRequest) -> CourierResponse:
    """Met à jour la position GPS d'un coursier (appelé par l'app mobile du coursier)."""
    try:
        courier = fleet_manager.update_courier_position(code, payload.lat, payload.lon)
    except KeyError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    return _courier_to_response(courier)


@router.put(
    "/couriers/{code}/active",
    response_model=CourierResponse,
    tags=["Coursiers"],
    summary="Activer ou désactiver un coursier",
)
def set_courier_active(code: str, active: bool) -> CourierResponse:
    """
    Active ou désactive un coursier (ex: fin de service, panne, pause).
    Un coursier inactif ne reçoit plus aucune attribution.
    """
    courier = fleet_manager.get_courier(code)
    if courier is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Coursier '{code.upper()}' introuvable.",
        )
    fleet_manager.set_courier_active(code, active)
    return _courier_to_response(courier)
