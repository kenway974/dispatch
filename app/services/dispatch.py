"""
Moteur de dispatch — cœur du système d'attribution automatique.

Algorithme en 3 étapes pour chaque commande entrante :
  1. FILTRAGE  : ne conserver que les coursiers éligibles (zone + véhicule + capacité)
  2. SCORING   : calculer un score composite pour chaque éligible
  3. SÉLECTION : attribuer la commande au coursier avec le score le plus bas

Formule de scoring (plus bas = meilleur coursier) :
  score = distance_base + pénalité_charge - bonus_groupage

  - distance_base  : distance Haversine entre la position du coursier et le ramassage
  - pénalité_charge: dissuade d'attribuer à un coursier déjà chargé
  - bonus_groupage : récompense si le trajet actuel passe déjà près du ramassage
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from app.config import (
    ELIGIBLE_ZONES_BY_VEHICLE,
    VOLUME_WEIGHTS,
    MAX_LOAD_BY_VEHICLE,
    GROUPAGE_PROXIMITY_KM,
    GROUPAGE_DISCOUNT_FACTOR,
    LOAD_PENALTY_PER_UNIT,
)
from app.models.courier import Courier, GpsPosition
from app.models.enums import VehicleType, VolumeType, OrderStatus
from app.models.order import Order
from app.services.fleet import FleetManager
from app.services.geo import haversine, min_distance_to_route


@dataclass
class DispatchResult:
    """
    Résultat retourné après chaque tentative d'attribution.

    Attributes:
        success        : True si un coursier a été trouvé et assigné.
        order_id       : ID de la commande traitée.
        assigned_to    : Code du coursier assigné (None si échec).
        score          : Score calculé pour ce coursier (None si échec).
        reason         : Message explicatif (succès ou raison de l'échec).
        eligible_count : Nombre de coursiers éligibles évalués.
    """
    success: bool
    order_id: str
    assigned_to: Optional[str]
    score: Optional[float]
    reason: str
    eligible_count: int


def is_courier_eligible(courier: Courier, order: Order) -> bool:
    """
    Détermine si un coursier peut prendre en charge une commande donnée.

    Règles d'éligibilité :
    1. Le coursier doit être actif.
    2. Son type de véhicule doit couvrir la zone de la commande.
       Exception : le fourgon peut prendre un colis Voiture dans N'IMPORTE QUELLE zone
       (un scooter ne peut pas transporter un colis de type Voiture).
    3. Le colis Voiture est réservé au fourgon (les scooters ne peuvent pas le porter).
    4. Le coursier ne doit pas être à capacité maximale pour ce volume.

    Args:
        courier : Coursier à évaluer.
        order   : Commande à attribuer.

    Returns:
        True si le coursier peut légalement prendre cette commande.
    """
    # Règle 1 : coursier connecté et disponible
    if not courier.is_active:
        return False

    # Règle 3 : colis Voiture → fourgon uniquement
    if order.volume_type == VolumeType.VOITURE and courier.vehicle_type != VehicleType.FOURGON:
        return False

    # Règle 2 : vérification zone/véhicule
    # Le fourgon couvre sa zone normale (Grande_Couronne)
    # MAIS peut aussi prendre un Voiture dans toute zone (déjà filtré ci-dessus)
    eligible_zones = ELIGIBLE_ZONES_BY_VEHICLE[courier.vehicle_type]
    zone_ok = order.zone in eligible_zones

    # Exception fourgon : s'il transporte un Voiture, la zone n'est pas contraignante
    if courier.vehicle_type == VehicleType.FOURGON and order.volume_type == VolumeType.VOITURE:
        zone_ok = True

    if not zone_ok:
        return False

    # Règle 4 : vérifier que le colis tient dans la capacité restante
    order_weight = VOLUME_WEIGHTS[order.volume_type]
    if courier.current_load + order_weight > MAX_LOAD_BY_VEHICLE[courier.vehicle_type]:
        return False

    return True


def score_courier(courier: Courier, order: Order) -> float:
    """
    Calcule le score d'un coursier pour une commande donnée.
    Un score bas = meilleur candidat.

    Composantes du score :

    1. distance_base (km)
       Distance Haversine entre la position actuelle du coursier et le point
       de ramassage de la commande. C'est le facteur dominant.

    2. pénalité_charge (km équivalents)
       = current_load × LOAD_PENALTY_PER_UNIT
       Pénalise les coursiers déjà très chargés pour équilibrer la flotte.

    3. bonus_groupage (km équivalents, soustrait)
       Si le point de ramassage est à moins de GROUPAGE_PROXIMITY_KM de l'un
       des waypoints du trajet actuel, on réduit le score de
       distance_base × GROUPAGE_DISCOUNT_FACTOR.
       → Priorité au coursier déjà dans le même quartier pour éviter les croisements.

    Args:
        courier : Coursier éligible à scorer.
        order   : Commande à attribuer.

    Returns:
        Score numérique (float). Plus bas = plus prioritaire.
    """
    pickup_pos = GpsPosition(lat=order.pickup.lat, lon=order.pickup.lon)

    # 1. Distance de base : position actuelle → ramassage
    base_distance = haversine(courier.position, pickup_pos)

    # 2. Pénalité de charge : décourager les coursiers proches de la saturation
    load_penalty = courier.current_load * LOAD_PENALTY_PER_UNIT

    # 3. Bonus de groupage : coursier déjà dans le quartier du ramassage ?
    groupage_bonus = 0.0
    if courier.assigned_orders:
        nearest_waypoint_dist = min_distance_to_route(courier, pickup_pos)
        if nearest_waypoint_dist <= GROUPAGE_PROXIMITY_KM:
            # Le coursier passe déjà près de ce ramassage → forte priorité
            groupage_bonus = base_distance * GROUPAGE_DISCOUNT_FACTOR

    final_score = base_distance + load_penalty - groupage_bonus

    return final_score


def get_eligible_couriers(order: Order, fleet: FleetManager) -> List[Courier]:
    """
    Filtre et retourne la liste des coursiers actifs éligibles pour une commande.

    Args:
        order : Commande à attribuer.
        fleet : Gestionnaire de la flotte.

    Returns:
        Liste (potentiellement vide) de coursiers éligibles.
    """
    return [
        courier
        for courier in fleet.get_active_couriers()
        if is_courier_eligible(courier, order)
    ]


def find_best_courier(order: Order, fleet: FleetManager) -> Optional[tuple[Courier, float]]:
    """
    Trouve le meilleur coursier pour une commande en scorant tous les éligibles.

    Args:
        order : Commande à attribuer.
        fleet : État courant de la flotte.

    Returns:
        Tuple (meilleur_coursier, score) ou None si aucun éligible.
    """
    eligible = get_eligible_couriers(order, fleet)

    if not eligible:
        return None

    # Score chaque éligible et trie par score croissant
    scored: List[tuple[float, Courier]] = [
        (score_courier(c, order), c)
        for c in eligible
    ]
    scored.sort(key=lambda x: x[0])

    best_score, best_courier = scored[0]
    return best_courier, best_score


def dispatch_order(order: Order, fleet: FleetManager) -> DispatchResult:
    """
    Point d'entrée principal du moteur de dispatch.

    Exécute le pipeline complet pour une commande :
      1. Cherche le meilleur coursier
      2. Si trouvé, effectue l'attribution via le FleetManager
      3. Retourne un DispatchResult détaillé

    Args:
        order : Commande nouvellement reçue (statut PENDING).
        fleet : Gestionnaire de la flotte (lu + modifié).

    Returns:
        DispatchResult avec le résultat de l'opération.
    """
    eligible_couriers = get_eligible_couriers(order, fleet)
    eligible_count = len(eligible_couriers)

    result = find_best_courier(order, fleet)

    if result is None:
        # Aucun coursier disponible → marquer la commande comme non attribuable
        order.status = OrderStatus.UNASSIGNABLE
        return DispatchResult(
            success=False,
            order_id=order.id,
            assigned_to=None,
            score=None,
            reason=(
                f"Aucun coursier éligible pour la zone '{order.zone}' "
                f"avec le volume '{order.volume_type}'."
            ),
            eligible_count=eligible_count,
        )

    best_courier, best_score = result

    # Effectue l'attribution dans le store
    fleet.assign_order_to_courier(order, best_courier.code)

    return DispatchResult(
        success=True,
        order_id=order.id,
        assigned_to=best_courier.code,
        score=round(best_score, 3),
        reason=(
            f"Coursier '{best_courier.code}' assigné "
            f"(distance score: {best_score:.2f} km, "
            f"charge: {best_courier.current_load}/{best_courier.max_load})."
        ),
        eligible_count=eligible_count,
    )
