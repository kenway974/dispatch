"""
Moteur de dispatch — cœur du système d'attribution automatique.

Pipeline pour chaque commande entrante :
  1. FILTRAGE   — coursiers éligibles (zone + véhicule + capacité)
  2. SCORING    — score composite par coursier
  3. SÉLECTION  — attribution au score le plus bas

─────────────────────────────────────────────────
FORMULE DE SCORING (plus bas = meilleur coursier)
─────────────────────────────────────────────────
  score = distance_base
        + pénalité_charge      (réduite si urgence)
        + pénalité_véhicule    (réduite si premium)
        − bonus_groupage       (désactivé si urgence > seuil)

Pénalités véhicule (pour orienter sans bloquer) :
  • scoot_banlieue_loin en Petite Couronne  → +2 km (hors zone principale)
  • longue_distance sur trajet < 25 km      → +10 km (préférer scooters)
  • fourgon sur petit volume + trajet < 15km → +6 km (préférer scooters)
  → toutes réduites à 40 % pour un client Premium
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from app.config import (
    ELIGIBLE_ZONES_BY_VEHICLE,
    FOURGON_SMALL_TRIP_MAX_KM,
    FOURGON_SMALL_TRIP_PENALTY_KM,
    GROUPAGE_DISCOUNT_FACTOR,
    GROUPAGE_PROXIMITY_KM,
    LOAD_PENALTY_PER_UNIT,
    LONG_TRIP_MIN_KM,
    LONGUE_DISTANCE_SHORT_TRIP_PENALTY_KM,
    MAX_LOAD_BY_VEHICLE,
    PREMIUM_PENALTY_FACTOR,
    SCOOT_LOIN_IN_PC_PENALTY_KM,
    URGENCY_GROUPAGE_DISABLE_THRESHOLD,
    URGENCY_LOAD_PENALTY_MIN_FACTOR,
    VOLUME_WEIGHTS,
)
from app.models.coursier import Coursier, GpsPosition
from app.models.enums import ClientTier, OrderStatus, VehicleType, VolumeType, Zone
from app.models.order import Order
from app.services.fleet import FleetManager
from app.services.geo import haversine, min_distance_to_route


@dataclass
class DispatchResult:
    """
    Résultat d'une tentative d'attribution.

    Attributes:
        success        : True si un coursier a été trouvé et assigné.
        order_id       : ID de la commande traitée.
        assigned_to    : Code du coursier assigné (None si échec).
        score          : Score calculé (None si échec).
        reason         : Message explicatif humain.
        eligible_count : Nombre de coursiers évalués.
    """
    success: bool
    order_id: str
    assigned_to: Optional[str]
    score: Optional[float]
    reason: str
    eligible_count: int


# ---------------------------------------------------------------------------
# Éligibilité
# ---------------------------------------------------------------------------

def is_coursier_eligible(coursier: Coursier, order: Order) -> bool:
    """
    Vérifie qu'un coursier peut légalement prendre cette commande.

    Règles :
    1. Coursier actif.
    2. Colis Voiture → fourgon ou longue_distance uniquement (trop volumineux pour scooter).
    3. La zone de livraison doit être dans les zones autorisées du véhicule.
    4. La charge actuelle + poids du colis ne doit pas dépasser la capacité max.
    """
    # Règle 1 : actif
    if not coursier.is_active:
        return False

    # Règle 2 : colis Voiture — réservé aux véhicules adaptés
    if order.volume_type == VolumeType.VOITURE:
        if coursier.vehicle_type not in (VehicleType.FOURGON, VehicleType.LONGUE_DISTANCE):
            return False

    # Règle 3 : zone
    if order.zone not in ELIGIBLE_ZONES_BY_VEHICLE[coursier.vehicle_type]:
        return False

    # Règle 4 : capacité
    order_weight = VOLUME_WEIGHTS[order.volume_type]
    if coursier.current_load + order_weight > MAX_LOAD_BY_VEHICLE[coursier.vehicle_type]:
        return False

    return True


# ---------------------------------------------------------------------------
# Pénalité véhicule sous-optimal
# ---------------------------------------------------------------------------

def _vehicle_sub_optimal_penalty(
    coursier: Coursier,
    order: Order,
    trip_km: float,
    penalty_factor: float,
) -> float:
    """
    Calcule la pénalité (km équivalents) pour un véhicule non-idéal sur cette course.

    Ces pénalités n'excluent pas le coursier — elles le défavorisent simplement
    face à un véhicule plus adapté. Si aucun meilleur candidat n'est disponible,
    il sera quand même sélectionné.

    Args:
        coursier        : Coursier évalué.
        order          : Commande à attribuer.
        trip_km        : Distance ramassage → livraison (pré-calculée).
        penalty_factor : Multiplicateur (réduit à 40 % pour les clients Premium).
    """
    vtype   = coursier.vehicle_type
    penalty = 0.0

    # scoot_banlieue_loin affecté en Petite Couronne
    # → hors zone principale (GC), les scoot_banlieue_proche sont prioritaires sur PC
    if vtype == VehicleType.SCOOT_BANLIEUE_LOIN and order.zone == Zone.PETITE_COURONNE:
        penalty += SCOOT_LOIN_IN_PC_PENALTY_KM

    # longue_distance sur court trajet
    # → spécialisé pour les gros trajets ; scooters plus agiles en ville
    if vtype == VehicleType.LONGUE_DISTANCE and trip_km < LONG_TRIP_MIN_KM:
        penalty += LONGUE_DISTANCE_SHORT_TRIP_PENALTY_KM

    # fourgon sur petit volume + court trajet
    # → privilégier un scooter, moins coûteux et plus manœuvrable
    if (
        vtype == VehicleType.FOURGON
        and order.volume_type != VolumeType.VOITURE
        and trip_km < FOURGON_SMALL_TRIP_MAX_KM
    ):
        penalty += FOURGON_SMALL_TRIP_PENALTY_KM

    return penalty * penalty_factor


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score_coursier(coursier: Coursier, order: Order) -> float:
    """
    Calcule le score d'un coursier pour une commande. Plus bas = meilleur.

    Composantes :

    1. distance_base (km)
       Distance Haversine entre la position actuelle du coursier et le ramassage.
       Facteur dominant du score.

    2. pénalité_charge (km équivalents)
       = charge_actuelle × LOAD_PENALTY_PER_UNIT × facteur_urgence
       Équilibre la flotte. Réduite à mesure que l'urgence augmente
       (un coursier chargé vaut mieux qu'un coursier lointain si c'est urgent).

    3. pénalité_véhicule (km équivalents)
       Pénalise les véhicules non-idéaux pour cette course.
       Réduite à 40 % pour les clients Premium.

    4. bonus_groupage (km équivalents, soustrait)
       Si le ramassage est proche du trajet actuel du coursier, il est déjà dans
       le quartier → priorité forte pour éviter les croisements de trajets.
       Désactivé si urgence > URGENCY_GROUPAGE_DISABLE_THRESHOLD.

    Args:
        coursier : Coursier éligible à évaluer.
        order   : Commande à attribuer.

    Returns:
        Score numérique ≥ 0.01. Plus bas = plus prioritaire.
    """
    pickup_pos   = GpsPosition(lat=order.pickup.lat,   lon=order.pickup.lon)
    delivery_pos = GpsPosition(lat=order.delivery.lat, lon=order.delivery.lon)

    urgency        = order.urgency_score                              # 0.0 → 1.0
    penalty_factor = PREMIUM_PENALTY_FACTOR if order.is_premium else 1.0
    trip_km        = haversine(pickup_pos, delivery_pos)

    # 1. Distance de base : position coursier → point de ramassage
    base_distance = haversine(coursier.position, pickup_pos)

    # 2. Pénalité de charge — allégée linéairement avec l'urgence
    load_factor  = max(URGENCY_LOAD_PENALTY_MIN_FACTOR, 1.0 - urgency)
    load_penalty = coursier.current_load * LOAD_PENALTY_PER_UNIT * load_factor

    # 3. Pénalité véhicule sous-optimal
    vehicle_penalty = _vehicle_sub_optimal_penalty(coursier, order, trip_km, penalty_factor)

    # 4. Bonus groupage (désactivé si trop urgent)
    groupage_bonus = 0.0
    if coursier.assigned_orders and urgency < URGENCY_GROUPAGE_DISABLE_THRESHOLD:
        nearest_waypoint_dist = min_distance_to_route(coursier, pickup_pos)
        if nearest_waypoint_dist <= GROUPAGE_PROXIMITY_KM:
            # Réduction proportionnelle à l'inverse de l'urgence
            groupage_bonus = base_distance * GROUPAGE_DISCOUNT_FACTOR * (1.0 - urgency * 2)

    return max(0.01, base_distance + load_penalty + vehicle_penalty - groupage_bonus)


# ---------------------------------------------------------------------------
# Sélection du meilleur coursier
# ---------------------------------------------------------------------------

def get_coursiers_eligibles(order: Order, fleet: FleetManager) -> List[Coursier]:
    """Retourne tous les coursiers actifs éligibles pour cette commande."""
    return [c for c in fleet.get_active_coursiers() if is_coursier_eligible(c, order)]


def find_best_coursier(order: Order, fleet: FleetManager) -> Optional[tuple[Coursier, float]]:
    """
    Évalue tous les coursiers éligibles et retourne le meilleur.

    Returns:
        (meilleur_coursier, score) ou None si aucun éligible.
    """
    eligible = get_coursiers_eligibles(order, fleet)
    if not eligible:
        return None

    scored = [(score_coursier(c, order), c) for c in eligible]
    scored.sort(key=lambda x: x[0])
    best_score, best_coursier = scored[0]
    return best_coursier, best_score


# ---------------------------------------------------------------------------
# Point d'entrée principal
# ---------------------------------------------------------------------------

def dispatch_order(order: Order, fleet: FleetManager) -> DispatchResult:
    """
    Lance le pipeline complet de dispatch pour une commande.

    1. Compte les éligibles
    2. Cherche le meilleur
    3. Attribue via FleetManager
    4. Retourne un DispatchResult détaillé

    Args:
        order : Commande avec statut PENDING.
        fleet : État courant de la flotte (lu + modifié).
    """
    eligible_count = len(get_coursiers_eligibles(order, fleet))
    result         = find_best_coursier(order, fleet)

    if result is None:
        order.status = OrderStatus.UNASSIGNABLE
        urgency_hint = f" (urgence : {order.urgency_score:.0%})" if order.deadline_minutes else ""
        tier_hint    = " [PREMIUM]" if order.is_premium else ""
        return DispatchResult(
            success=False,
            order_id=order.id,
            assigned_to=None,
            score=None,
            reason=(
                f"Aucun coursier éligible{tier_hint} pour la zone «{order.zone}»"
                f" avec le volume «{order.volume_type}»{urgency_hint}."
            ),
            eligible_count=eligible_count,
        )

    best_coursier, best_score = result
    fleet.assign_order_to_coursier(order, best_coursier.code)

    urgency_label = f" | urgence {order.urgency_score:.0%}" if order.deadline_minutes else ""
    tier_label    = " [PREMIUM]" if order.is_premium else ""

    return DispatchResult(
        success=True,
        order_id=order.id,
        assigned_to=best_coursier.code,
        score=round(best_score, 3),
        reason=(
            f"Coursier «{best_coursier.code}» assigné{tier_label}"
            f" — score {best_score:.2f} km"
            f" | charge {best_coursier.current_load}/{best_coursier.max_load}"
            f"{urgency_label}."
        ),
        eligible_count=eligible_count,
    )
