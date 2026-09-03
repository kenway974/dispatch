"""
Énumérations métier du moteur de dispatch.
Centralise tous les types fixes pour éviter les magic strings dans le code.
"""

from enum import Enum


class VehicleType(str, Enum):
    """
    Les véhicules de la flotte, tous électriques.

    Ce qui limite un scooter en Grande Couronne n'est pas le permis mais
    l'autonomie de sa batterie — et elle dépend autant du coursier que de la
    machine : certains emportent des batteries de rechange. C'est donc le
    coursier qui porte l'attribut `autonomie_etendue`, pas son type de véhicule.
    """
    SCOOT_50 = "scoot_50"
    # 50 cm³ électrique — Paris et banlieue proche.
    # Boulogne, Clichy, Levallois, Montreuil, Pantin, Aubervilliers.
    # Accepté en Petite Couronne, mais on préfère y envoyer un 125.

    SCOOT_125 = "scoot_125"
    # 125 électrique — le cheval de trait de la flotte.
    # Paris et Petite Couronne en priorité ; Grande Couronne pour ceux qui ont
    # l'autonomie (Saint-Ouen-l'Aumône, Versailles, Cergy, Grigny).

    VOITURE = "voiture"
    # Voiture électrique — toutes zones.

    FOURGON = "fourgon"
    # Utilitaire électrique — toutes zones, seul à porter un colis Voiture.


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
    - Voiture  : très volumineux, réservé au fourgon ou voiture.
    """
    STANDARD = "Standard"
    VOLUME = "Volume"
    VOITURE = "Voiture"


class ClientTier(str, Enum):
    """
    Niveau de priorité du client.
    - Standard : règles de dispatch normales.
    - Premium  : pénalités véhicule réduites (fourgon / voiture moins hésitants),
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
