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

    position: Optional["PositionSchema"] = Field(
        default=None,
        description="Position exploitable (GPS récent ou estimée) et sa fraîcheur",
    )


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


# ---------------------------------------------------------------------------
# Schémas du mode pilote (comparaison manuel / automatique)
# ---------------------------------------------------------------------------

class ComparaisonRequest(BaseModel):
    """
    Corps de POST /pilote/comparaison — une course + le choix du dispatcheur.

    `choix_manuel` est saisi AVANT la révélation du choix de l'application :
    c'est ce qui garantit que l'essai mesure quelque chose.
    """
    id: Optional[str] = Field(default=None, description="Référence de la course (auto-générée si absente)")
    pickup_lat: float   = Field(..., ge=-90,  le=90)
    pickup_lon: float   = Field(..., ge=-180, le=180)
    delivery_lat: float = Field(..., ge=-90,  le=90)
    delivery_lon: float = Field(..., ge=-180, le=180)
    zone: Zone
    volume_type: VolumeType
    client_tier: ClientTier = Field(default=ClientTier.STANDARD)
    deadline_minutes: Optional[int] = Field(default=None, ge=1)

    choix_manuel: Optional[str] = Field(
        default=None, min_length=2, max_length=4,
        description="Code du coursier réellement choisi par le dispatcheur",
    )
    commentaire: Optional[str] = Field(
        default=None, max_length=500,
        description="Note libre : pourquoi ce choix (contexte que le moteur ne voit pas)",
    )
    pickup_adresse: Optional[str]   = Field(default=None, max_length=300)
    delivery_adresse: Optional[str] = Field(default=None, max_length=300)


class CourseExistanteRequest(BaseModel):
    """
    Corps de POST /coursiers/{code}/courses — déclarer une course déjà en cours.

    Sert au début de service : le dispatcheur renseigne ce que chacun a déjà
    en portefeuille, sinon le moteur raisonne sur une flotte vide et son
    équilibrage de charge n'a aucun sens.
    """
    id: Optional[str] = Field(default=None, description="Référence (auto-générée si absente)")
    pickup_lat: float   = Field(..., ge=-90,  le=90)
    pickup_lon: float   = Field(..., ge=-180, le=180)
    delivery_lat: float = Field(..., ge=-90,  le=90)
    delivery_lon: float = Field(..., ge=-180, le=180)
    zone: Zone
    volume_type: VolumeType = Field(default=VolumeType.STANDARD)
    ramassage_effectue: bool = Field(
        default=False,
        description="Le colis est déjà dans la sacoche — l'itinéraire ne repasse plus par le ramassage",
    )


class PingPositionRequest(BaseModel):
    """
    Corps de POST /coursiers/{code}/ping — position remontée par le téléphone.

    Envoyé automatiquement par la page `/suivi/{code}` que le coursier laisse
    ouverte pendant son service.
    """
    lat: float = Field(..., ge=-90,  le=90)
    lon: float = Field(..., ge=-180, le=180)
    precision_m: Optional[float] = Field(
        default=None, ge=0,
        description="Précision annoncée par le GPS, en mètres (indicative)",
    )


class PositionSchema(BaseModel):
    """Position exploitable d'un coursier, avec sa fraîcheur et sa provenance."""
    lat: float
    lon: float
    source: str                # 'gps', 'manuelle' ou 'estimee'
    age_secondes: int
    perimee: bool
    temps_reel: bool
    distance_parcourue_km: float
    explication: str


class PositionImportee(BaseModel):
    """Une position reprise du système de suivi déjà en place dans l'entreprise."""
    code: str = Field(..., min_length=2, max_length=4, description="Code coursier (ex: KEN)")
    lat: float = Field(..., ge=-90,  le=90)
    lon: float = Field(..., ge=-180, le=180)
    horodatage: Optional[datetime] = Field(
        default=None,
        description=(
            "Instant de la MESURE côté système source. Sans lui, une position "
            "relevée il y a dix minutes serait affichée comme temps réel."
        ),
    )


class ImportPositionsRequest(BaseModel):
    """Corps de POST /positions/import — un lot de positions."""
    positions: List[PositionImportee] = Field(..., min_length=1, max_length=500)
