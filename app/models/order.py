"""
Modèles de données pour les commandes (orders).
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field

from app.models.enums import Zone, VolumeType, OrderStatus, ClientTier


class Coordinates(BaseModel):
    """Point GPS (latitude / longitude)."""
    lat: float = Field(..., ge=-90.0, le=90.0,   description="Latitude en degrés décimaux")
    lon: float = Field(..., ge=-180.0, le=180.0, description="Longitude en degrés décimaux")

    def __str__(self) -> str:
        return f"({self.lat:.5f}, {self.lon:.5f})"


class Order(BaseModel):
    """
    Représente une commande reçue par le système.

    Champs clés pour le dispatch :
    - zone / volume_type : déterminent les coursiers éligibles
    - client_tier        : Premium réduit les pénalités véhicule et passe en priorité
    - deadline_minutes   : minutes allouées depuis la création ; alimente urgency_score
    """
    model_config = {"frozen": False}

    id: str = Field(..., description="Identifiant unique (ex: ORD-001)")
    pickup: Coordinates  = Field(..., description="Point de ramassage")
    delivery: Coordinates = Field(..., description="Point de livraison")
    zone: Zone            = Field(..., description="Zone géographique de livraison")
    volume_type: VolumeType = Field(..., description="Catégorie de volume du colis")

    client_tier: ClientTier = Field(
        default=ClientTier.STANDARD,
        description="Niveau client : Premium réduit les pénalités et passe prioritaire",
    )
    deadline_minutes: Optional[int] = Field(
        default=None,
        ge=1,
        description=(
            "Délai de livraison souhaité en minutes depuis la création. "
            "None = pas de contrainte de temps."
        ),
    )

    status: OrderStatus    = Field(default=OrderStatus.PENDING)
    assigned_courier: Optional[str] = Field(default=None)
    created_at: datetime   = Field(default_factory=datetime.now)

    # ------------------------------------------------------------------
    # Propriétés calculées (non sérialisées dans la réponse JSON)
    # ------------------------------------------------------------------

    @property
    def urgency_score(self) -> float:
        """
        Score d'urgence normalisé entre 0.0 (aucune contrainte) et 1.0 (deadline dépassée).

        Formule : 1 - (minutes_restantes / deadline_total)
        - 0.0  → pas de deadline ou largement dans les temps
        - 0.5  → 50 % du temps écoulé
        - 1.0  → deadline dépassée
        """
        if self.deadline_minutes is None:
            return 0.0
        elapsed_min  = (datetime.now() - self.created_at).total_seconds() / 60.0
        remaining    = self.deadline_minutes - elapsed_min
        if remaining <= 0:
            return 1.0
        return max(0.0, 1.0 - remaining / self.deadline_minutes)

    @property
    def is_premium(self) -> bool:
        return self.client_tier == ClientTier.PREMIUM
