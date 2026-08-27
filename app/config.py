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
#   voiture → 8  (véhicule adapté aux gros volumes sur route)
#   fourgon         → 10 (seul à pouvoir porter un Voiture = 5 unités)
# ---------------------------------------------------------------------------
MAX_LOAD_BY_VEHICLE: dict[VehicleType, int] = {
    VehicleType.SCOOT_50:   5,
    VehicleType.SCOOT_125:  5,
    VehicleType.VOITURE:    8,
    VehicleType.FOURGON:   10,
}

# ---------------------------------------------------------------------------
# Zones autorisées par type de véhicule
#
#   scoot_50            → Paris seulement (50cc, pas de voies rapides)
#   scoot_50  → Paris + Petite Couronne (50cc, PAS de Grande Couronne)
#   scoot_125    → Petite Couronne + Grande Couronne (125cc+, voies rapides OK)
#   voiture        → Toutes zones (spécialisé inter-villes / aéroports)
#   fourgon                → Toutes zones (seul à pouvoir livrer les colis Voiture)
# ---------------------------------------------------------------------------
ELIGIBLE_ZONES_BY_VEHICLE: dict[VehicleType, list[Zone]] = {
    VehicleType.SCOOT_50:  [Zone.PARIS, Zone.PETITE_COURONNE],
    VehicleType.SCOOT_125: [Zone.PARIS, Zone.PETITE_COURONNE],
    VehicleType.VOITURE:   [Zone.PARIS, Zone.PETITE_COURONNE, Zone.GRANDE_COURONNE],
    VehicleType.FOURGON:   [Zone.PARIS, Zone.PETITE_COURONNE, Zone.GRANDE_COURONNE],
}

# La Grande Couronne n'est pas fermée aux scooters : elle est ouverte à ceux qui
# ont l'autonomie pour. Un 125 avec des batteries de rechange y va, un 50 non.
# L'attribut se porte sur le coursier (`autonomie_etendue`), pas sur le véhicule.
VEHICULES_AUTONOMIE_ETENDUE_POSSIBLE: set[VehicleType] = {VehicleType.SCOOT_125}

# ---------------------------------------------------------------------------
# Paramètres de scoring de base
# ---------------------------------------------------------------------------

# Pénalité de charge : km équivalents ajoutés par unité de charge portée
# → dissuade d'attribuer à un coursier très chargé
LOAD_PENALTY_PER_UNIT: float = 0.4

# Seuil de proximité d'un point de tournée, en km.
# N'entre plus dans le score depuis le passage au détour marginal — qui mesure
# directement ce que la course rallonge — mais reste utilisé par les tests
# géographiques et l'affichage.
GROUPAGE_PROXIMITY_KM: float = 2.0

# ---------------------------------------------------------------------------
# Pénalités de sous-optimalité véhicule
# (permettent la flexibilité sans exclure les véhicules non-idéaux)
# ---------------------------------------------------------------------------

# Un 50 en Petite Couronne : accepté, mais on préfère y envoyer un 125.
# Pénalité volontairement légère — s'il est le mieux placé ou déjà sur sa
# tournée, il y va quand même.
SCOOT_50_EN_PETITE_COURONNE_PENALITE_KM: float = 1.5

# Voiture sur trajet court : un scooter passe mieux dans Paris.
LONG_TRIP_MIN_KM: float = 25.0
VOITURE_SHORT_TRIP_PENALTY_KM: float = 4.0   # pénalité si trajet < seuil

# fourgon sur petit volume ET court trajet : gaspillage, préférer les scooters
FOURGON_SMALL_TRIP_MAX_KM: float = 15.0               # seuil en km
FOURGON_SMALL_TRIP_PENALTY_KM: float = 6.0            # pénalité si conditions remplies

# ---------------------------------------------------------------------------
# Paramètres client Premium
# ---------------------------------------------------------------------------

# Facteur appliqué aux pénalités de sous-optimalité pour les commandes premium
# 0.4 → les pénalités sont réduites à 40 % de leur valeur normale
# Concrètement : fourgon / voiture hésitent moins à prendre une course premium
PREMIUM_PENALTY_FACTOR: float = 0.4

# ---------------------------------------------------------------------------
# Paramètres d'urgence (deadline)
# ---------------------------------------------------------------------------

# Facteur minimal de la pénalité de charge quand urgence = 1.0 (deadline dépassée)
# 0.1 → à urgence max, la pénalité charge ne représente plus que 10 % de sa valeur
URGENCY_LOAD_PENALTY_MIN_FACTOR: float = 0.1


# ---------------------------------------------------------------------------
# Estimation de position (mode pilote)
# ---------------------------------------------------------------------------
# Vitesse moyenne réellement tenue, trajets urbains porte-à-porte : elle intègre
# les arrêts, les feux et le stationnement, elle est donc bien inférieure à la
# vitesse de pointe du véhicule.
VITESSE_MOYENNE_KMH: dict[VehicleType, float] = {
    VehicleType.SCOOT_50:  18.0,   # bridé, circulation dense
    VehicleType.SCOOT_125: 24.0,   # voies rapides accessibles
    VehicleType.VOITURE:   20.0,
    VehicleType.FOURGON:   14.0,   # gabarit + stationnement difficile
}

# Au-delà de ce délai sans signal, la position est signalée comme périmée.
# Elle reste utilisée — mieux vaut une position vieille que pas de position —
# mais l'interface le dit clairement au dispatcheur.
POSITION_PERIMEE_MINUTES: float = 20.0

# Une position GPS plus récente que ce seuil est considérée comme temps réel.
POSITION_TEMPS_REEL_SECONDES: float = 120.0

# ---------------------------------------------------------------------------
# Paramètres propres à Lungta
#
# Renseignés à partir des informations publiques de l'entreprise et des usages
# du métier. À corriger avec le dispatcheur avant l'essai : ce sont des valeurs
# de départ, pas des vérités.
# ---------------------------------------------------------------------------

# Adresse du dépôt — position par défaut d'un coursier nouvellement déclaré.
# Géocodée côté navigateur au premier usage : aucune coordonnée n'est inventée ici.
DEPOT_ADRESSE: str = "24 rue des Dames, 75017 Paris"

# Niveaux d'urgence proposés au dispatcheur, en minutes.
# Le moteur ne connaît que des minutes ; ces paliers sont là pour que la saisie
# se fasse dans les mots du métier plutôt qu'en tapant un nombre.
NIVEAUX_URGENCE: list[tuple[str, int]] = [
    ("Prioritaire", 30),
    ("Express",     60),
    ("Urgent",      90),
    ("Normal",     180),
]

# Amplitude de service, utilisée pour cadrer la fenêtre de comparaison.
AMPLITUDE_SERVICE: tuple[str, str] = ("08:30", "19:00")

# ---------------------------------------------------------------------------
# Protection des courses urgentes déjà embarquées
# ---------------------------------------------------------------------------
# Marge de sécurité conservée sur chaque livraison à échéance, en minutes.
# Un coursier attendu à 15h30 alors qu'il est 14h47 ne doit pas être encombré :
# le retard induit par une nouvelle course est comparé au temps qu'il lui reste,
# cette marge déduite.
MARGE_SECURITE_MINUTES: float = 10.0

# Pénalité, en km équivalents, par minute de retard induite sur une course déjà
# embarquée qui a une échéance. Une minute de retard coûte donc environ trois
# minutes de trajet : faire rater une livraison promise vaut plus cher que
# quelques kilomètres, sans pour autant écraser tous les autres postes.
#
# Calibré par simulation — scripts/simulation_journee.py, 5 journées de 100
# courses. Au-delà, le moteur déverse le fourgon et le longue distance sur des
# courses parisiennes dès que les scooters prennent du retard, et davantage de
# courses finissent sans personne :
#
#     0,5 → 14,5 % de véhicules lourds sur course Paris ·  3 non attribuées
#     1,0 → 16,4 %                                      ·  7
#     3,0 → 17,1 %                                      ·  9
#     5,0 → 18,5 %                                      ·  7
#
# À réviser dès que l'historique réel sera disponible : ces chiffres sortent
# d'une journée simulée, pas de la vraie exploitation.
PENALITE_RETARD_PAR_MINUTE: float = 1.0


# ---------------------------------------------------------------------------
# Ce qu'on porte décide de ce qu'on peut accepter
#
# Règle du terrain : ce n'est pas la distance qui autorise un détour, c'est
# l'urgence de ce qu'on a déjà sur soi. Un coursier à côté du ramassage mais
# porteur d'une urgence n'est pas le bon choix ; un coursier plus loin qui ne
# porte que du souple peut remonter sans que ça pose problème.
# ---------------------------------------------------------------------------

# En dessous de ce temps restant, une course portée compte comme une urgence.
SEUIL_URGENCE_MINUTES: float = 60.0

# Ajouté au score quand une course urgente est proposée à un coursier qui en
# porte déjà une. On n'empile pas deux urgences sur le même dos : la deuxième
# ferait rater la première, ou l'inverse.
PENALITE_URGENCES_CUMULEES_KM: float = 8.0

# Appliqué au détour d'un coursier qui ne porte aucune urgence. Il est libre de
# ses mouvements : un crochet lui coûte moins cher qu'à quelqu'un qui court
# après une échéance.
FACTEUR_DETOUR_SANS_URGENCE: float = 0.6


# Marge conservée avant la pause ou la fin de service d'un coursier, en minutes.
# On ne l'envoie pas sur une course qui finirait pile à l'heure de son sandwich.
MARGE_AVANT_ARRET_MINUTES: float = 10.0


# ---------------------------------------------------------------------------
# Ré-attribution d'une course en cours
#
# Le dispatcheur reprend une course déjà attribuée et la bascule sur un autre
# coursier. Le colis passe de main à main : les deux doivent donc être au même
# endroit au moment de la passation. Ce n'est pas un lieu fixe — c'est là où ils
# se croisent, le bureau ou ailleurs.
# ---------------------------------------------------------------------------

# Distance maximale entre deux coursiers pour qu'ils puissent se passer un colis.
DISTANCE_PASSATION_MAX_KM: float = 0.4

# Gain minimal, en km équivalents, pour qu'une ré-attribution soit proposée.
# En dessous, la manipulation coûte plus de temps à deux personnes qu'elle n'en
# fait gagner.
GAIN_MINIMUM_ECHANGE_KM: float = 3.0


# ---------------------------------------------------------------------------
# Le temps qui ne se voit pas sur la carte
# ---------------------------------------------------------------------------

# Minutes passées à chaque arrêt : trouver la porte, monter, attendre à
# l'accueil, faire signer. Six étages sans ascenseur coûtent bien davantage,
# mais c'est une moyenne — l'historique QuickDriver permettra de la caler,
# puisque le coursier valide au moment RÉEL du ramassage et de la livraison.
MINUTES_PAR_ARRET: float = 3.0

# Marge appliquée aux temps de trajet estimés. Un itinéraire annoncé à 24
# minutes n'en fait jamais 24 : rue barrée, manifestation, livraison en double
# file devant. On prévoit large plutôt que de promettre juste.
MARGE_TRAJET: float = 1.25


# ---------------------------------------------------------------------------
# Là où le moteur cesse de trancher seul
#
# « Ça me ferait péter un câble qu'un robot me dise que non, en fait, tu n'as
# pas fini, tu continues de travailler. »
#
# Dans ces cas, le moteur désigne toujours le meilleur candidat, mais il le
# marque : à valider avec l'intéressé. Le dispatcheur négocie, puis tranche.
# ---------------------------------------------------------------------------

# En deçà de ce temps avant la fin de service, on ne décide plus à sa place.
SEUIL_VALIDATION_FIN_SERVICE_MINUTES: float = 30.0

# Détour supplémentaire, en km, au-delà duquel une course prise sur le trajet
# retour cesse d'aller de soi. En dessous : ça ne le perturbe pas, on attribue.
SEUIL_VALIDATION_DETOUR_RETOUR_KM: float = 2.0
