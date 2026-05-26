"""
Modèles de données pour les coursiers (couriers).
"""

from __future__ import annotations

from typing import List
from pydantic import BaseModel, Field, field_validator

from app.models.enums import VehicleType, VolumeType
from app.config import VOLUME_WEIGHTS, MAX_LOAD_BY_VEHICLE


class GpsPosition(BaseModel):
    """Position GPS temps réel d'un coursier."""
    lat: float = Field(..., ge=-90.0, le=90.0)
    lon: float = Field(..., ge=-180.0, le=180.0)

    def __str__(self) -> str:
        return f"({self.lat:.5f}, {self.lon:.5f})"


class AssignedOrder(BaseModel):
    """
    Snapshot léger d'une commande stockée dans la liste du coursier.
    Évite les références circulaires et permet un accès direct aux waypoints.
    """
    order_id: str
    pickup_lat: float
    pickup_lon: float
    delivery_lat: float
    delivery_lon: float
    volume_type: VolumeType

    @property
    def weight(self) -> int:
        """Poids en unités de charge de cette commande."""
        return VOLUME_WEIGHTS[self.volume_type]

    @property
    def pickup_position(self) -> GpsPosition:
        return GpsPosition(lat=self.pickup_lat, lon=self.pickup_lon)

    @property
    def delivery_position(self) -> GpsPosition:
        return GpsPosition(lat=self.delivery_lat, lon=self.delivery_lon)


class Courier(BaseModel):
    """
    Représente un coursier de la flotte avec son état temps réel.

    Attributs clés :
    - code           : identifiant unique 3 lettres (ex: KEN)
    - vehicle_type   : détermine les zones et volumes éligibles
    - position       : position GPS actualisée en temps réel
    - assigned_orders: liste des courses actuellement assignées
    - is_active      : False si le coursier est hors service / déconnecté
    """
    model_config = {"frozen": False}

    code: str = Field(..., min_length=3, max_length=3, description="Code unique 3 lettres (ex: KEN)")
    vehicle_type: VehicleType
    position: GpsPosition
    assigned_orders: List[AssignedOrder] = Field(default_factory=list)
    is_active: bool = Field(default=True, description="Coursier disponible et connecté")

    @field_validator("code")
    @classmethod
    def code_must_be_uppercase(cls, v: str) -> str:
        """Force le code en majuscules pour la cohérence."""
        return v.upper()

    @property
    def current_load(self) -> int:
        """
        Charge totale en unités abstraites.
        Standard=1, Volume=2, Voiture=5.
        """
        return sum(o.weight for o in self.assigned_orders)

    @property
    def max_load(self) -> int:
        """Capacité maximale selon le type de véhicule."""
        return MAX_LOAD_BY_VEHICLE[self.vehicle_type]

    @property
    def remaining_capacity(self) -> int:
        """Unités de charge disponibles avant saturation."""
        return self.max_load - self.current_load

    @property
    def is_at_capacity(self) -> bool:
        """True si le coursier ne peut plus accepter aucune commande."""
        return self.current_load >= self.max_load

    @property
    def order_count(self) -> int:
        """Nombre de courses actuellement en portefeuille."""
        return len(self.assigned_orders)
