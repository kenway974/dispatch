"""
Utilitaires géographiques pour le calcul de distances.

Utilise la formule de Haversine pour calculer la distance orthodromique
(distance à vol d'oiseau sur la sphère terrestre) entre deux points GPS.
Précision suffisante pour les distances intra-urbaines (< 100 km).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
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


@dataclass
class Arret:
    """
    Un point que le coursier doit desservir.

    `course_id` relie un ramassage à sa livraison : c'est la seule contrainte
    d'ordre du métier. Pour le reste, l'itinéraire suit la géographie — chez
    Lungta on ne ramasse pas tout avant de livrer, on enchaîne les points dans
    l'ordre qui a du sens sur la carte.
    """
    position: GpsPosition
    course_id: str
    est_livraison: bool


def _precedence_respectee(sequence: List[Arret]) -> bool:
    """Vrai si chaque livraison est précédée du ramassage de sa course."""
    ramasses: set[str] = set()
    for arret in sequence:
        if arret.est_livraison:
            if arret.course_id not in ramasses:
                return False
        else:
            ramasses.add(arret.course_id)
    return True


def _longueur(depart: GpsPosition, sequence: List[Arret]) -> float:
    """Kilomètres parcourus en desservant `sequence` depuis `depart`."""
    return total_route_distance([depart] + [a.position for a in sequence])


def ordonner_tournee(depart: GpsPosition, arrets: List[Arret]) -> tuple[List[Arret], float]:
    """
    Reconstruit l'ordre de passage le plus court, ramassage avant livraison.

    Le métier ne sépare pas les ramassages des livraisons : un coursier peut
    enchaîner un ramassage, une livraison, cinq ramassages, puis neuf livraisons
    d'un coup. Ce qui décide, c'est la pertinence géographique du point suivant —
    pas son type. Supposer l'ordre d'attribution reviendrait à mesurer une
    tournée que personne ne fait.

    Construction au plus proche voisin parmi les arrêts réalisables, puis
    amélioration par inversions de segments (2-opt) qui préservent la
    précédence. Sur les quelques arrêts que porte un coursier, cela donne
    l'optimum ou s'en approche de très près, en un temps négligeable.

    Returns:
        (ordre de passage, kilomètres totaux depuis `depart`).
    """
    if not arrets:
        return [], 0.0

    # ── Construction au plus proche voisin ──────────────────────────────────
    restants = list(arrets)
    ramasses: set[str] = set()
    sequence: List[Arret] = []
    courant = depart

    while restants:
        realisables = [
            a for a in restants
            if not a.est_livraison or a.course_id in ramasses
        ]
        if not realisables:
            # Livraison orpheline (ramassage déjà effectué avant la mesure) :
            # on la traite comme réalisable plutôt que de bloquer.
            realisables = restants

        prochain = min(realisables, key=lambda a: haversine(courant, a.position))
        sequence.append(prochain)
        restants.remove(prochain)
        if not prochain.est_livraison:
            ramasses.add(prochain.course_id)
        courant = prochain.position

    # ── Amélioration 2-opt sous contrainte de précédence ────────────────────
    meilleure = _longueur(depart, sequence)
    ameliore = True
    while ameliore:
        ameliore = False
        for i in range(len(sequence) - 1):
            for j in range(i + 1, len(sequence)):
                candidate = sequence[:i] + sequence[i:j + 1][::-1] + sequence[j + 1:]
                if not _precedence_respectee(candidate):
                    continue
                longueur = _longueur(depart, candidate)
                if longueur < meilleure - 1e-9:
                    sequence, meilleure, ameliore = candidate, longueur, True

    return sequence, meilleure


def cout_insertion(
    depart: GpsPosition,
    tournee: List[Arret],
    nouveaux_arrets: List[Arret],
) -> float:
    """
    Kilomètres supplémentaires imposés par l'ajout d'arrêts à une tournée.

    C'est la question que le coursier se pose vraiment : « si je prends ça,
    combien ça me rallonge ? » — et non « à quelle distance est le ramassage ? ».
    Une course dont le ramassage est à 200 m mais dont la livraison le fait
    repartir en arrière lui coûte plus cher qu'une course dont le ramassage est
    à 1 km mais dont la livraison est sur sa route.

    Les deux itinéraires — sans puis avec les nouveaux arrêts — sont réordonnés
    indépendamment : accepter une course peut réorganiser toute la suite du
    parcours, et c'est bien cette tournée réorganisée qu'il faut comparer.

    Returns:
        Kilomètres ajoutés. Comprend le trajet propre de la nouvelle course.
    """
    _, sans = ordonner_tournee(depart, tournee)
    _, avec = ordonner_tournee(depart, tournee + nouveaux_arrets)
    return avec - sans


def detour_marginal(
    depart: GpsPosition,
    tournee: List[Arret],
    ramassage: GpsPosition,
    livraison: GpsPosition,
    course_id: str = "NOUVELLE",
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
    nouveaux = [
        Arret(position=ramassage, course_id=course_id, est_livraison=False),
        Arret(position=livraison, course_id=course_id, est_livraison=True),
    ]
    return cout_insertion(depart, tournee, nouveaux) - haversine(ramassage, livraison)
