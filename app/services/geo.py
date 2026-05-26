"""
Utilitaires géographiques pour le calcul de distances.

Utilise la formule de Haversine pour calculer la distance orthodromique
(distance à vol d'oiseau sur la sphère terrestre) entre deux points GPS.
Précision suffisante pour les distances intra-urbaines (< 100 km).
"""

from __future__ import annotations

import math
from typing import List

from app.models.courier import GpsPosition, Courier


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


def get_route_waypoints(courier: Courier) -> List[GpsPosition]:
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
        courier: Coursier dont on extrait les waypoints.

    Returns:
        Liste ordonnée de positions GPS (position actuelle + ramassages + livraisons).
    """
    waypoints: List[GpsPosition] = [courier.position]

    for assigned in courier.assigned_orders:
        waypoints.append(assigned.pickup_position)
        waypoints.append(assigned.delivery_position)

    return waypoints


def min_distance_to_route(courier: Courier, target: GpsPosition) -> float:
    """
    Calcule la distance minimale entre un point cible et tous les waypoints
    du trajet actuel d'un coursier.

    Utilisé pour détecter les opportunités de groupage :
    si cette distance est inférieure à GROUPAGE_PROXIMITY_KM, le coursier
    est déjà « dans le coin » du nouveau point de ramassage.

    Args:
        courier: Coursier avec ses courses en cours.
        target : Point GPS du nouveau ramassage à évaluer.

    Returns:
        Distance minimale en km entre le target et le trajet actuel.
        Retourne float('inf') si le coursier n'a pas de courses.
    """
    waypoints = get_route_waypoints(courier)

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
