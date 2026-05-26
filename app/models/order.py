"""
Modèles de données pour les commandes (orders).
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, model_validator

from app.models.enums import Zone, VolumeType, OrderStatus


class Coordinates(BaseModel):
    """Point GPS (latitude / longitude)."""
    lat: float = Field(..., ge=-90.0, le=90.0, description="Latitude en degrés décimaux")
    lon: float = Field(..., ge=-180.0, le=180.0, description="Longitude en degrés décimaux")

    def __str__(self) -> str:
        return f"({self.lat:.5f}, {self.lon:.5f})"


class Order(BaseModel):
    """
    Représente une commande reçue par le système.

    Contient toutes les informations nécessaires au moteur de dispatch
    pour trouver le meilleur coursier : zone, volume, coordonnées GPS.
    """
    model_config = {"frozen": False}

    id: str = Field(..., description="Identifiant unique de la commande (ex: ORD-001)")
    pickup: Coordinates = Field(..., description="Coordonnées GPS du point de ramassage")
    delivery: Coordinates = Field(..., description="Coordonnées GPS du point de livraison")
    zone: Zone = Field(..., description="Zone géographique de livraison")
    volume_type: VolumeType = Field(..., description="Type de volume du colis")
    status: OrderStatus = Field(default=OrderStatus.PENDING, description="Statut courant")
    assigned_courier: Optional[str] = Field(
        default=None,
        description="Code 3 lettres du coursier attribué (None si pas encore attribué)"
    )
    created_at: datetime = Field(default_factory=datetime.now)

    @model_validator(mode="after")
    def validate_voiture_needs_fourgon_zone(self) -> "Order":
        """
        Un colis Voiture peut être accepté dans toutes les zones
        (le fourgon peut couvrir Paris si nécessaire pour ce type).
        Aucune restriction de zone supplémentaire ici — géré dans dispatch.py.
        """
        return self
