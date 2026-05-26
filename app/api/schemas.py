"""
Schémas Pydantic pour les requêtes et réponses de l'API REST.
Séparés des modèles métier pour découpler la sérialisation HTTP de la logique interne.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from app.models.enums import VehicleType, Zone, VolumeType, OrderStatus, ClientTier


# ---------------------------------------------------------------------------
# Schémas de requête
# ---------------------------------------------------------------------------

class CreateOrderRequest(BaseModel):
    """Corps de la requête POST /orders."""
    id: str            = Field(..., description="Identifiant unique (ex: ORD-042)")
    pickup_lat: float  = Field(..., ge=-90,  le=90,  description="Latitude du ramassage")
    pickup_lon: float  = Field(..., ge=-180, le=180, description="Longitude du ramassage")
    delivery_lat: float = Field(..., ge=-90,  le=90,  description="Latitude de la livraison")
    delivery_lon: float = Field(..., ge=-180, le=180, description="Longitude de la livraison")
    zone: Zone          = Field(..., description="Zone géographique")
    volume_type: VolumeType = Field(..., description="Catégorie de volume")
    client_tier: ClientTier = Field(
        default=ClientTier.STANDARD,
        description="Niveau client : standard ou premium",
    )
    deadline_minutes: Optional[int] = Field(
        default=None, ge=1,
        description="Délai de livraison souhaité en minutes (None = pas de contrainte)",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "id": "ORD-001",
                "pickup_lat": 48.8559, "pickup_lon": 2.3578,
                "delivery_lat": 48.8864, "delivery_lon": 2.3432,
                "zone": "Paris",
                "volume_type": "Standard",
                "client_tier": "standard",
                "deadline_minutes": 45,
            }
        }
    }


class CreateCoursierRequest(BaseModel):
    """Corps de la requête POST /coursiers."""
    code: str = Field(..., min_length=2, max_length=4, description="Code 2-4 lettres (ex: KEN, JC)")
    vehicle_type: VehicleType
    lat: float = Field(..., ge=-90,  le=90)
    lon: float = Field(..., ge=-180, le=180)


class UpdatePositionRequest(BaseModel):
    """Corps de la requête PUT /coursiers/{code}/position."""
    lat: float = Field(..., ge=-90,  le=90)
    lon: float = Field(..., ge=-180, le=180)


class UpdateCoursierRequest(BaseModel):
    """Corps de la requête PATCH /coursiers/{code} — mise à jour partielle."""
    vehicle_type: Optional[VehicleType] = Field(default=None, description="Nouveau type de véhicule")
    adresse: Optional[str]  = Field(default=None, description="Nouvelle adresse (géocodée côté serveur)")
    lat: Optional[float]    = Field(default=None, ge=-90,  le=90)
    lon: Optional[float]    = Field(default=None, ge=-180, le=180)
    is_active: Optional[bool] = Field(default=None, description="Activer ou désactiver le coursier")


# ---------------------------------------------------------------------------
# Schémas de réponse
# ---------------------------------------------------------------------------

class AssignedOrderSchema(BaseModel):
    """Course dans le portefeuille d'un coursier (snapshot léger)."""
    order_id: str
    pickup_lat: float
    pickup_lon: float
    delivery_lat: float
    delivery_lon: float
    volume_type: VolumeType
    weight: int


class CoursierResponse(BaseModel):
    """État complet d'un coursier."""
    code: str
    vehicle_type: VehicleType
    lat: float
    lon: float
    is_active: bool
    current_load: int
    max_load: int
    remaining_capacity: int
    order_count: int
    assigned_orders: List[AssignedOrderSchema]


class OrderResponse(BaseModel):
    """État complet d'une commande."""
    id: str
    pickup_lat: float
    pickup_lon: float
    delivery_lat: float
    delivery_lon: float
    zone: Zone
    volume_type: VolumeType
    client_tier: ClientTier
    deadline_minutes: Optional[int]
    status: OrderStatus
    assigned_coursier: Optional[str]
    created_at: datetime


class DispatchResponse(BaseModel):
    """Résultat d'une tentative d'attribution."""
    success: bool
    order_id: str
    assigned_to: Optional[str]
    score: Optional[float]
    reason: str
    eligible_count: int
    order: OrderResponse


class HealthResponse(BaseModel):
    """Statut général du système."""
    status: str
    coursier_count: int
    order_count: int
    coursiers_actifs: int
