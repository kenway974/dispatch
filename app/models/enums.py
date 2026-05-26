"""
Énumérations métier du moteur de dispatch.
Centralise tous les types fixes pour éviter les magic strings dans le code.
"""

from enum import Enum


class VehicleType(str, Enum):
    """Type de véhicule de chaque coursier, détermine la zone éligible."""
    SCOOT_VILLE = "scoot_ville"                     # Paris intra-muros uniquement
    SCOOT_BANLIEUE_PROCHE = "scoot_banlieue_proche" # Petite Couronne uniquement
    SCOOT_BANLIEUE_LOIN = "scoot_banlieue_loin"     # Grande Couronne uniquement
    FOURGON = "fourgon"                             # Grande Couronne + colis Voiture


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
    - Voiture  : très volumineux, réservé au fourgon.
    """
    STANDARD = "Standard"
    VOLUME = "Volume"
    VOITURE = "Voiture"


class OrderStatus(str, Enum):
    """Cycle de vie d'une commande dans le système."""
    PENDING = "pending"           # Commande reçue, en attente d'attribution
    ASSIGNED = "assigned"         # Attribuée à un coursier
    IN_TRANSIT = "in_transit"     # En cours de livraison
    DELIVERED = "delivered"       # Livrée avec succès
    UNASSIGNABLE = "unassignable" # Aucun coursier éligible trouvé
