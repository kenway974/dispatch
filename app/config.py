"""
Configuration centrale du moteur de dispatch.
Tous les seuils et paramètres métier sont ici — aucune magic number dans le code.
"""

from app.models.enums import VehicleType, Zone, VolumeType

# ---------------------------------------------------------------------------
# Poids des colis (unités de charge abstraites)
# ---------------------------------------------------------------------------
VOLUME_WEIGHTS: dict[VolumeType, int] = {
    VolumeType.STANDARD: 1,   # petit colis
    VolumeType.VOLUME:   2,   # colis encombrant
    VolumeType.VOITURE:  5,   # très volumineux
}

# ---------------------------------------------------------------------------
# Capacité maximale par type de véhicule (en unités de charge)
#
#   scooters        → 5  (ex: 5 Standard  OU  2 Volume + 1 Standard)
#   longue_distance → 8  (véhicule adapté aux gros volumes sur route)
#   fourgon         → 10 (seul à pouvoir porter un Voiture = 5 unités)
# ---------------------------------------------------------------------------
MAX_LOAD_BY_VEHICLE: dict[VehicleType, int] = {
    VehicleType.SCOOT_VILLE:            5,
    VehicleType.SCOOT_BANLIEUE_PROCHE:  5,
    VehicleType.SCOOT_BANLIEUE_LOIN:    5,
    VehicleType.LONGUE_DISTANCE:        8,
    VehicleType.FOURGON:               10,
}

# ---------------------------------------------------------------------------
# Zones autorisées par type de véhicule
#
#   scoot_ville            → Paris seulement (50cc, pas de voies rapides)
#   scoot_banlieue_proche  → Paris + Petite Couronne (50cc, PAS de Grande Couronne)
#   scoot_banlieue_loin    → Petite Couronne + Grande Couronne (125cc+, voies rapides OK)
#   longue_distance        → Toutes zones (spécialisé inter-villes / aéroports)
#   fourgon                → Toutes zones (seul à pouvoir livrer les colis Voiture)
# ---------------------------------------------------------------------------
ELIGIBLE_ZONES_BY_VEHICLE: dict[VehicleType, list[Zone]] = {
    VehicleType.SCOOT_VILLE:           [Zone.PARIS],
    VehicleType.SCOOT_BANLIEUE_PROCHE: [Zone.PARIS, Zone.PETITE_COURONNE],
    VehicleType.SCOOT_BANLIEUE_LOIN:   [Zone.PETITE_COURONNE, Zone.GRANDE_COURONNE],
    VehicleType.LONGUE_DISTANCE:       [Zone.PARIS, Zone.PETITE_COURONNE, Zone.GRANDE_COURONNE],
    VehicleType.FOURGON:               [Zone.PARIS, Zone.PETITE_COURONNE, Zone.GRANDE_COURONNE],
}

# ---------------------------------------------------------------------------
# Paramètres de scoring de base
# ---------------------------------------------------------------------------

# Pénalité de charge : km équivalents ajoutés par unité de charge portée
# → dissuade d'attribuer à un coursier très chargé
LOAD_PENALTY_PER_UNIT: float = 0.4

# Seuil de proximité pour déclencher le bonus de groupage (km)
GROUPAGE_PROXIMITY_KM: float = 2.0

# Réduction de score si groupage détecté (0.5 = −50%)
GROUPAGE_DISCOUNT_FACTOR: float = 0.5

# ---------------------------------------------------------------------------
# Pénalités de sous-optimalité véhicule
# (permettent la flexibilité sans exclure les véhicules non-idéaux)
# ---------------------------------------------------------------------------

# scoot_banlieue_loin en Petite Couronne : hors zone principale, légère pénalité
# → les scoot_banlieue_proche restent prioritaires sur PC
SCOOT_LOIN_IN_PC_PENALTY_KM: float = 2.0

# longue_distance sur trajet court : les scooters sont plus adaptés et moins coûteux
LONG_TRIP_MIN_KM: float = 25.0                         # seuil en km
LONGUE_DISTANCE_SHORT_TRIP_PENALTY_KM: float = 10.0   # pénalité si trajet < seuil

# fourgon sur petit volume ET court trajet : gaspillage, préférer les scooters
FOURGON_SMALL_TRIP_MAX_KM: float = 15.0               # seuil en km
FOURGON_SMALL_TRIP_PENALTY_KM: float = 6.0            # pénalité si conditions remplies

# ---------------------------------------------------------------------------
# Paramètres client Premium
# ---------------------------------------------------------------------------

# Facteur appliqué aux pénalités de sous-optimalité pour les commandes premium
# 0.4 → les pénalités sont réduites à 40 % de leur valeur normale
# Concrètement : fourgon / longue_distance hésitent moins à prendre une course premium
PREMIUM_PENALTY_FACTOR: float = 0.4

# ---------------------------------------------------------------------------
# Paramètres d'urgence (deadline)
# ---------------------------------------------------------------------------

# Facteur minimal de la pénalité de charge quand urgence = 1.0 (deadline dépassée)
# 0.1 → à urgence max, la pénalité charge ne représente plus que 10 % de sa valeur
URGENCY_LOAD_PENALTY_MIN_FACTOR: float = 0.1

# Au-delà de ce score d'urgence, le bonus de groupage est désactivé
# → on ne cherche plus à optimiser les trajets, on prend le plus proche
URGENCY_GROUPAGE_DISABLE_THRESHOLD: float = 0.5
