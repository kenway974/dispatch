"""
Constantes de configuration du moteur de dispatch.
Modifier ces valeurs pour ajuster les seuils métier sans toucher à la logique.
"""

from app.models.enums import VehicleType, Zone, VolumeType

# ---------------------------------------------------------------------------
# Poids par type de volume (unités de charge abstraites)
# Standard=1, Volume=2, Voiture=5
# ---------------------------------------------------------------------------
VOLUME_WEIGHTS: dict[VolumeType, int] = {
    VolumeType.STANDARD: 1,
    VolumeType.VOLUME: 2,
    VolumeType.VOITURE: 5,
}

# ---------------------------------------------------------------------------
# Capacité maximale (en unités de charge) par type de véhicule
# scooters : 5 unités  → 5 Standard  OU  2 Volume + 1 Standard  OU  2 Volume
# fourgon  : 10 unités → peut porter jusqu'à 1 Voiture (5) + 5 Standard
# ---------------------------------------------------------------------------
MAX_LOAD_BY_VEHICLE: dict[VehicleType, int] = {
    VehicleType.SCOOT_VILLE: 5,
    VehicleType.SCOOT_BANLIEUE_PROCHE: 5,
    VehicleType.SCOOT_BANLIEUE_LOIN: 5,
    VehicleType.FOURGON: 10,
}

# ---------------------------------------------------------------------------
# Zones autorisées par type de véhicule
# Le fourgon gère aussi les colis Voiture quelle que soit la zone (cf. dispatch.py)
# ---------------------------------------------------------------------------
ELIGIBLE_ZONES_BY_VEHICLE: dict[VehicleType, list[Zone]] = {
    VehicleType.SCOOT_VILLE: [Zone.PARIS],
    VehicleType.SCOOT_BANLIEUE_PROCHE: [Zone.PETITE_COURONNE],
    VehicleType.SCOOT_BANLIEUE_LOIN: [Zone.GRANDE_COURONNE],
    VehicleType.FOURGON: [Zone.GRANDE_COURONNE],
}

# ---------------------------------------------------------------------------
# Paramètres de scoring et de groupage
# ---------------------------------------------------------------------------

# Distance (km) sous laquelle un coursier est considéré « dans le même quartier »
# que le point de ramassage → déclenche le bonus de groupage
GROUPAGE_PROXIMITY_KM: float = 2.0

# Facteur de réduction appliqué à la distance de base lors d'un groupage
# 0.5 = la distance effective est réduite de 50 % dans le scoring
GROUPAGE_DISCOUNT_FACTOR: float = 0.5

# Pénalité (en km équivalents) par unité de charge déjà portée
# Permet de préférer un coursier plus proche et moins chargé
LOAD_PENALTY_PER_UNIT: float = 0.4
