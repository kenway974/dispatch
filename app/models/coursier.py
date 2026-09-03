"""
Modèles de données pour les coursiers.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator

from app.models.enums import PositionSource, VehicleType, VolumeType
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

    ramassage_effectue: bool = Field(
        default=False,
        description=(
            "Le colis est déjà dans la sacoche. L'itinéraire ne repasse alors plus "
            "par le point de ramassage — sinon le moteur croit le coursier obligé de "
            "retraverser Paris pour un colis qu'il a déjà sur lui."
        ),
    )

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


class Coursier(BaseModel):
    """
    Représente un coursier de la flotte avec son état temps réel.

    Attributs clés :
    - code           : identifiant unique 2-4 lettres (ex: KEN, JC)
    - vehicle_type   : détermine les zones et volumes éligibles
    - position       : DERNIÈRE position connue — jamais une estimation.
                       Les estimations sont recalculées à la lecture, pour ne pas
                       accumuler l'erreur d'estimation dans l'état stocké.
    - assigned_orders: liste des courses actuellement assignées
    - is_active      : False si le coursier est hors service / déconnecté
    """
    model_config = {"frozen": False}

    code: str = Field(..., min_length=2, max_length=4, description="Code unique 2-4 lettres (ex: KEN, JC)")
    vehicle_type: VehicleType
    position: GpsPosition
    assigned_orders: List[AssignedOrder] = Field(default_factory=list)
    is_active: bool = Field(default=True, description="Coursier disponible et connecté")

    position_updated_at: datetime = Field(
        default_factory=datetime.now,
        description="Horodatage de la dernière position connue — sert à mesurer sa fraîcheur",
    )
    position_source: PositionSource = Field(
        default=PositionSource.MANUELLE,
        description="D'où vient cette position : saisie du dispatcheur ou GPS du coursier",
    )

    autonomie_etendue: bool = Field(
        default=False,
        description=(
            "Il emporte des batteries de rechange et accepte la Grande Couronne. "
            "C'est une caractéristique du coursier, pas de sa machine : deux 125 "
            "identiques n'ont pas le même rayon selon qui les conduit."
        ),
    )

    debut_pause: Optional[datetime] = Field(
        default=None,
        description="Heure à laquelle il s'arrête pour manger. None = non renseignée.",
    )
    retour_depot: Optional[GpsPosition] = Field(
        default=None,
        description=(
            "Où il rentre en fin de service. Renseigné, ce retour devient le "
            "dernier point de sa tournée : une course sur le chemin ne lui coûte "
            "presque rien, une course à l'opposé le fait dévier pour rien."
        ),
    )
    fin_service: Optional[datetime] = Field(
        default=None,
        description="Heure à laquelle il rend son scooter. None = non renseignée.",
    )

    @property
    def prochain_arret(self) -> Optional[datetime]:
        """
        Le premier des deux qui arrive : sa pause ou sa fin de service.

        Une course qu'il ne peut pas terminer avant cet instant n'a rien à faire
        chez lui — elle finirait au bureau ou pas du tout.
        """
        moments = [m for m in (self.debut_pause, self.fin_service) if m is not None]
        return min(moments) if moments else None

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
