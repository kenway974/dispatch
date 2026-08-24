"""
Utilitaires géographiques pour le calcul de distances.

Utilise la formule de Haversine pour calculer la distance orthodromique
(distance à vol d'oiseau sur la sphère terrestre) entre deux points GPS.
Précision suffisante pour les distances intra-urbaines (< 100 km).
"""

from __future__ import annotations

import math
from typing import List

from app.models.coursier import GpsPosition, Coursier


# Rayon moyen de la Terre en kilomètres
EARTH_RADIUS_KM: float = 6371.0


def haversine(p1: GpsPosition, p2: GpsPosition) -> float:
    """
    Calcule la distance orthodromique entre deux points GPS (formule de Haversine).

    Args:
        p1: Premier point (lat/lon en degrés décimaux).
        p2: Deuxième point (lat/lon en degrés décimaux).

    Returns:
        Distance en kilomètres (float).

    Exemple:
        >>> haversine(GpsPosition(lat=48.8566, lon=2.3522), GpsPosition(lat=48.8864, lon=2.3432))
        3.37  # ~3.4 km entre Paris centre et Montmartre
    """
    # Conversion degrés → radians
    lat1 = math.radians(p1.lat)
    lon1 = math.radians(p1.lon)
    lat2 = math.radians(p2.lat)
    lon2 = math.radians(p2.lon)

    # Différences de coordonnées
    d_lat = lat2 - lat1
    d_lon = lon2 - lon1

    # Formule de Haversine
    a = math.sin(d_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(d_lon / 2) ** 2
    c = 2 * math.asin(math.sqrt(a))

    return EARTH_RADIUS_KM * c


def get_route_waypoints(coursier: Coursier) -> List[GpsPosition]:
    """
    Retourne tous les waypoints significatifs du trajet actuel d'un coursier.

    Inclut :
    - La position GPS actuelle du coursier
    - Le point de ramassage de chaque course assignée
    - Le point de livraison de chaque course assignée

    Ces waypoints servent à détecter les opportunités de groupage :
    si le nouveau point de ramassage est proche de l'un de ces points,
    il est rentable de l'attribuer à ce coursier.

    Args:
        coursier: Coursier dont on extrait les waypoints.

    Returns:
        Liste ordonnée de positions GPS (position actuelle + ramassages + livraisons).
    """
    waypoints: List[GpsPosition] = [coursier.position]

    for assigned in coursier.assigned_orders:
        waypoints.append(assigned.pickup_position)
        waypoints.append(assigned.delivery_position)

    return waypoints


def min_distance_to_route(coursier: Coursier, target: GpsPosition) -> float:
    """
    Calcule la distance minimale entre un point cible et tous les waypoints
    du trajet actuel d'un coursier.

    Utilisé pour détecter les opportunités de groupage :
    si cette distance est inférieure à GROUPAGE_PROXIMITY_KM, le coursier
    est déjà « dans le coin » du nouveau point de ramassage.

    Args:
        coursier: Coursier avec ses courses en cours.
        target : Point GPS du nouveau ramassage à évaluer.

    Returns:
        Distance minimale en km entre le target et le trajet actuel.
        Retourne float('inf') si le coursier n'a pas de courses.
    """
    waypoints = get_route_waypoints(coursier)

    if not waypoints:
        return float("inf")

    return min(haversine(wp, target) for wp in waypoints)


def total_route_distance(positions: List[GpsPosition]) -> float:
    """
    Calcule la longueur totale d'une route comme somme des segments consécutifs.

    Args:
        positions: Liste ordonnée de points GPS formant le trajet.

    Returns:
        Distance totale en km. 0.0 si moins de 2 points.
    """
    if len(positions) < 2:
        return 0.0

    return sum(haversine(positions[i], positions[i + 1]) for i in range(len(positions) - 1))


def cout_insertion(
    depart: GpsPosition,
    itineraire: List[GpsPosition],
    ramassage: GpsPosition,
    livraison: GpsPosition,
) -> float:
    """
    Kilomètres supplémentaires imposés par l'insertion d'une course dans une tournée.

    C'est la question que le coursier se pose vraiment : « si je prends ça,
    combien ça me rallonge ? » — et non « à quelle distance est le ramassage ? ».
    Une course dont le ramassage est à 200 m mais dont la livraison le fait
    repartir en arrière lui coûte plus cher qu'une course dont le ramassage est à
    1 km mais dont la livraison est sur sa route.

    L'insertion est testée à toutes les positions possibles de la tournée
    restante (ramassage puis livraison, dans cet ordre), et la meilleure est
    retenue. Avec quelques arrêts par coursier, l'énumération est immédiate.

    Args:
        depart     : position actuelle du coursier.
        itineraire : points qu'il doit encore desservir, dans l'ordre.
        ramassage  : ramassage de la course évaluée.
        livraison  : livraison de la course évaluée.

    Returns:
        Kilomètres ajoutés à la tournée. Comprend le trajet de la course
        elle-même — retrancher `haversine(ramassage, livraison)` pour obtenir
        le seul détour.
    """
    points = [depart] + list(itineraire)
    base = total_route_distance(points)
    n = len(points)

    meilleur = float("inf")
    for i in range(1, n + 1):            # position du ramassage
        for j in range(i, n + 1):        # position de la livraison, jamais avant
            sequence = points[:i] + [ramassage] + points[i:j] + [livraison] + points[j:]
            meilleur = min(meilleur, total_route_distance(sequence))

    return meilleur - base


def detour_marginal(
    depart: GpsPosition,
    itineraire: List[GpsPosition],
    ramassage: GpsPosition,
    livraison: GpsPosition,
) -> float:
    """
    Détour net d'une course, une fois retranché son trajet propre.

    Le trajet ramassage → livraison est identique pour tous les coursiers : le
    laisser dans le score ne départagerait personne. Ce qui distingue, c'est le
    détour que chacun doit consentir.

    Returns:
        Détour en km. Zéro pour un coursier au repos pile sur le ramassage.
        **Négatif** quand la course recouvre une portion de tournée déjà prévue :
        le coursier est alors payé pour l'accepter, au sens du score.
    """
    return cout_insertion(depart, itineraire, ramassage, livraison) - haversine(ramassage, livraison)
