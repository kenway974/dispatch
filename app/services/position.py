"""
Où est ce coursier, maintenant ?

Le moteur note les coursiers sur leur distance au point de ramassage. Cette note
ne vaut donc rien de plus que la position sur laquelle elle est calculée. Or un
coursier bouge en permanence et personne ne va ressaisir douze positions à la main
entre deux courses.

Deux sources, par ordre de préférence :

1. **GPS** — le coursier ouvre `/suivi/{code}` sur son téléphone, le navigateur
   envoie sa position toutes les 30 secondes. Aucune application à installer.

2. **Estimation à l'estime** — faute de signal récent, on projette : dernier point
   connu, temps écoulé, vitesse moyenne du véhicule, et la suite du trajet déjà
   assigné. C'est exactement le raisonnement que fait le dispatcheur de tête ;
   ici il est simplement écrit.

Règle centrale : **une estimation n'est jamais écrite dans l'état**. La position
stockée reste le dernier point réellement connu, et l'estimation est recalculée à
chaque lecture. Sinon l'erreur s'accumulerait à chaque estimation d'estimation, et
au bout d'une heure le moteur raisonnerait sur une fiction.

Corollaire aussi important : la fraîcheur est renvoyée avec la position, et
affichée. Le dispatcheur doit pouvoir distinguer « KEN, GPS il y a 20 s » de
« KEN, estimé depuis sa livraison d'il y a 35 min » — la seconde mérite un coup
de téléphone avant de suivre la recommandation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from app.config import (
    POSITION_PERIMEE_MINUTES,
    POSITION_TEMPS_REEL_SECONDES,
    VITESSE_MOYENNE_KMH,
)
from app.models.coursier import Coursier, GpsPosition
from app.models.enums import PositionSource
from app.services.geo import haversine


@dataclass
class PositionEstimee:
    """
    Position exploitable d'un coursier, accompagnée de son degré de confiance.

    Attributes:
        position     : coordonnées à utiliser pour le scoring
        source       : 'gps', 'manuelle' ou 'estimee'
        age_secondes : ancienneté du dernier point réellement connu
        perimee      : True si le signal est trop vieux pour être fiable
        temps_reel   : True si le point GPS date de moins de deux minutes
        distance_parcourue_km : distance projetée depuis le dernier point connu
        explication  : phrase affichée telle quelle au dispatcheur
    """
    position: GpsPosition
    source: str
    age_secondes: float
    perimee: bool
    temps_reel: bool
    distance_parcourue_km: float
    explication: str

    def to_dict(self) -> dict:
        return {
            "lat": round(self.position.lat, 6),
            "lon": round(self.position.lon, 6),
            "source": self.source,
            "age_secondes": round(self.age_secondes),
            "perimee": self.perimee,
            "temps_reel": self.temps_reel,
            "distance_parcourue_km": round(self.distance_parcourue_km, 2),
            "explication": self.explication,
        }


def _formuler_age(secondes: float) -> str:
    """Ancienneté en toutes lettres, calibrée pour un coup d'œil rapide."""
    if secondes < 60:
        return f"il y a {int(secondes)} s"
    minutes = secondes / 60.0
    if minutes < 60:
        return f"il y a {int(minutes)} min"
    return f"il y a {minutes / 60.0:.1f} h"


def _itineraire_restant(coursier: Coursier) -> list[GpsPosition]:
    """
    Points que le coursier doit encore atteindre, dans l'ordre de son trajet.

    Approximation assumée : les courses sont desservies dans l'ordre où elles ont
    été attribuées, ramassage puis livraison. Le vrai ordre appartient au coursier
    et nous est inconnu ; sur des trajets urbains de quelques kilomètres, l'écart
    reste inférieur à l'imprécision qu'on cherche à corriger.
    """
    points: list[GpsPosition] = []
    for course in coursier.assigned_orders:
        points.append(course.pickup_position)
        points.append(course.delivery_position)
    return points


def _avancer_sur_itineraire(
    depart: GpsPosition,
    itineraire: list[GpsPosition],
    distance_km: float,
) -> tuple[GpsPosition, float]:
    """
    Fait avancer un point de `distance_km` le long d'un itinéraire.

    Returns:
        (position atteinte, distance réellement parcourue).
        Si l'itinéraire est plus court que la distance disponible, le coursier est
        supposé arrivé au dernier point et attendre : on ne l'envoie pas au-delà.
    """
    position = depart
    restant = distance_km
    parcourue = 0.0

    for cible in itineraire:
        segment = haversine(position, cible)
        if segment <= 0:
            continue
        if restant < segment:
            # Interpolation linéaire sur le segment. Sur quelques kilomètres,
            # l'écart avec une vraie interpolation sphérique est négligeable.
            ratio = restant / segment
            return (
                GpsPosition(
                    lat=position.lat + (cible.lat - position.lat) * ratio,
                    lon=position.lon + (cible.lon - position.lon) * ratio,
                ),
                parcourue + restant,
            )
        position = cible
        restant -= segment
        parcourue += segment

    return position, parcourue


def estimer_position(coursier: Coursier, maintenant: Optional[datetime] = None) -> PositionEstimee:
    """
    Détermine la position exploitable d'un coursier et la confiance qu'on peut y placer.

    Args:
        coursier   : le coursier à localiser.
        maintenant : instant de référence (injectable pour les tests).

    Returns:
        PositionEstimee — jamais None : à défaut de mieux, le dernier point connu
        est renvoyé, signalé comme périmé. Une position douteuse reste plus utile
        au dispatcheur qu'un trou dans son tableau.
    """
    maintenant = maintenant or datetime.now()
    age = max(0.0, (maintenant - coursier.position_updated_at).total_seconds())
    temps_reel = coursier.position_source == PositionSource.GPS and age <= POSITION_TEMPS_REEL_SECONDES
    perimee = age > POSITION_PERIMEE_MINUTES * 60.0

    # Point GPS frais : rien à estimer.
    if temps_reel:
        return PositionEstimee(
            position=coursier.position, source="gps", age_secondes=age,
            perimee=False, temps_reel=True, distance_parcourue_km=0.0,
            explication=f"Position GPS {_formuler_age(age)}",
        )

    itineraire = _itineraire_restant(coursier)
    libelle_source = "GPS" if coursier.position_source == PositionSource.GPS else "Position saisie"

    # Sans course en cours, le coursier est en attente : il n'y a rien à projeter.
    if not itineraire:
        return PositionEstimee(
            position=coursier.position,
            source=coursier.position_source.value,
            age_secondes=age, perimee=perimee, temps_reel=False,
            distance_parcourue_km=0.0,
            explication=f"{libelle_source} {_formuler_age(age)} — sans course en cours, supposé sur place",
        )

    vitesse = VITESSE_MOYENNE_KMH[coursier.vehicle_type]
    distance_disponible = vitesse * (age / 3600.0)
    position, parcourue = _avancer_sur_itineraire(coursier.position, itineraire, distance_disponible)

    if parcourue < 0.05:  # moins de 50 m : le déplacement n'est pas significatif
        return PositionEstimee(
            position=coursier.position,
            source=coursier.position_source.value,
            age_secondes=age, perimee=perimee, temps_reel=False,
            distance_parcourue_km=0.0,
            explication=f"{libelle_source} {_formuler_age(age)}",
        )

    return PositionEstimee(
        position=position, source="estimee", age_secondes=age,
        perimee=perimee, temps_reel=False, distance_parcourue_km=parcourue,
        explication=(
            f"Estimée : {parcourue:.1f} km parcourus sur sa tournée depuis "
            f"son dernier point connu ({_formuler_age(age)})"
        ),
    )


def position_effective(coursier: Coursier, maintenant: Optional[datetime] = None) -> GpsPosition:
    """Raccourci pour le scoring : la position à utiliser, sans son contexte."""
    return estimer_position(coursier, maintenant).position
