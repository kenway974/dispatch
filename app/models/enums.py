"""
Énumérations métier du moteur de dispatch.
Centralise tous les types fixes pour éviter les magic strings dans le code.
"""

from enum import Enum


class VehicleType(str, Enum):
    """
    Type de véhicule de chaque coursier.
    Détermine les zones éligibles et les règles de scoring.
    """
    SCOOT_VILLE = "scoot_ville"
    # Paris intra-muros uniquement (type historique, peu utilisé dans la flotte actuelle)

    SCOOT_BANLIEUE_PROCHE = "scoot_banlieue_proche"
    # Paris + Petite Couronne — pas de Grande Couronne

    SCOOT_BANLIEUE_LOIN = "scoot_banlieue_loin"
    # Grande Couronne (zone principale) + Petite Couronne (avec légère pénalité)

    LONGUE_DISTANCE = "longue_distance"
    # Toutes zones — spécialisé trajets > 25 km (inter-villes, aéroports)
    # Pénalisé sur les courts trajets : les scooters restent prioritaires

    FOURGON = "fourgon"
    # Toutes zones — seul à pouvoir porter des colis Voiture
    # Pénalisé sur petits volumes + courts trajets : éviter le gaspillage


class Zone(str, Enum):
    """Zone géographique de la commande (déterminée à la création)."""
    PARIS = "Paris"
    PETITE_COURONNE = "Petite_Couronne"
    GRANDE_COURONNE = "Grande_Couronne"


class VolumeType(str, Enum):
    """
    Catégorie de volume du colis.
    - Standard : petit colis, tient sur tout type de scooter.
    - Volume   : colis encombrant, nécessite une capacité suffisante.
    - Voiture  : très volumineux, réservé au fourgon ou longue_distance.
    """
    STANDARD = "Standard"
    VOLUME = "Volume"
    VOITURE = "Voiture"


class ClientTier(str, Enum):
    """
    Niveau de priorité du client.
    - Standard : règles de dispatch normales.
    - Premium  : pénalités véhicule réduites (fourgon / longue_distance moins hésitants),
                 et traitement prioritaire dans les files de dispatch en masse.
    """
    STANDARD = "standard"
    PREMIUM = "premium"


class OrderStatus(str, Enum):
    """Cycle de vie d'une commande dans le système."""
    PENDING = "pending"           # Reçue, en attente d'attribution
    ASSIGNED = "assigned"         # Attribuée à un coursier
    IN_TRANSIT = "in_transit"     # En cours de livraison
    DELIVERED = "delivered"       # Livrée avec succès
    UNASSIGNABLE = "unassignable" # Aucun coursier éligible trouvé


class PositionSource(str, Enum):
    """
    Provenance de la dernière position connue d'un coursier.

    Le dispatcheur doit savoir sur quoi le moteur raisonne : une recommandation
    fondée sur un point GPS de 30 secondes et une autre fondée sur une saisie
    d'il y a une heure n'ont pas la même valeur.
    """
    MANUELLE = "manuelle"   # saisie par le dispatcheur (adresse ou clic sur la carte)
    GPS      = "gps"        # remontée par le téléphone du coursier
    IMPORT   = "import"     # reprise du système de suivi déjà en place dans l'entreprise
