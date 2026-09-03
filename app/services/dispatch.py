"""
Moteur de dispatch — cœur du système d'attribution automatique.

Pipeline pour chaque commande entrante :
  1. FILTRAGE   — coursiers éligibles (zone + véhicule + capacité)
  2. SCORING    — score composite par coursier
  3. SÉLECTION  — attribution au score le plus bas

─────────────────────────────────────────────────
FORMULE DE SCORING (plus bas = meilleur coursier)
─────────────────────────────────────────────────
  score = distance_base
        + pénalité_charge      (réduite si urgence)
        + pénalité_véhicule    (réduite si premium)
        − bonus_groupage       (désactivé si urgence > seuil)

Pénalités véhicule (pour orienter sans bloquer) :
  • scoot_125 en Petite Couronne  → +2 km (hors zone principale)
  • voiture sur trajet < 25 km      → +10 km (préférer scooters)
  • fourgon sur petit volume + trajet < 15km → +6 km (préférer scooters)
  → toutes réduites à 40 % pour un client Premium
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from app.config import (
    ELIGIBLE_ZONES_BY_VEHICLE,
    VEHICULES_AUTONOMIE_ETENDUE_POSSIBLE,
    FOURGON_SMALL_TRIP_MAX_KM,
    FOURGON_SMALL_TRIP_PENALTY_KM,
    LOAD_PENALTY_PER_UNIT,
    LONG_TRIP_MIN_KM,
    VOITURE_SHORT_TRIP_PENALTY_KM,
    FACTEUR_DETOUR_SANS_URGENCE,
    MARGE_AVANT_ARRET_MINUTES,
    MARGE_TRAJET,
    MINUTES_PAR_ARRET,
    MARGE_SECURITE_MINUTES,
    PENALITE_RETARD_PAR_MINUTE,
    MAX_LOAD_BY_VEHICLE,
    PENALITE_URGENCES_CUMULEES_KM,
    PREMIUM_PENALTY_FACTOR,
    SEUIL_URGENCE_MINUTES,
    SEUIL_VALIDATION_DETOUR_RETOUR_KM,
    SEUIL_VALIDATION_FIN_SERVICE_MINUTES,
    VITESSE_MOYENNE_KMH,
    SCOOT_50_EN_PETITE_COURONNE_PENALITE_KM,
    URGENCY_LOAD_PENALTY_MIN_FACTOR,
    VOLUME_WEIGHTS,
)
from app.models.coursier import Coursier, GpsPosition
from app.models.enums import ClientTier, OrderStatus, VehicleType, VolumeType, Zone
from app.models.order import Order
from app.services.fleet import FleetManager
from app.services.geo import Arret, detour_marginal, haversine, ordonner_tournee
from app.services.position import position_effective


@dataclass
class DispatchResult:
    """
    Résultat d'une tentative d'attribution.

    Attributes:
        success        : True si un coursier a été trouvé et assigné.
        order_id       : ID de la commande traitée.
        assigned_to    : Code du coursier assigné (None si échec).
        score          : Score calculé (None si échec).
        reason         : Message explicatif humain.
        eligible_count : Nombre de coursiers évalués.
    """
    success: bool
    order_id: str
    assigned_to: Optional[str]
    score: Optional[float]
    reason: str
    eligible_count: int


# ---------------------------------------------------------------------------
# Éligibilité
# ---------------------------------------------------------------------------

def arrets_en_cours(coursier: Coursier) -> List[Arret]:
    """
    Arrêts qu'il reste à desservir, sans présumer de leur ordre.

    L'ordre de passage est reconstruit par `ordonner_tournee` : chez Lungta on
    n'enchaîne pas les ramassages avant les livraisons, on suit la carte.
    """
    arrets: List[Arret] = []
    for course in coursier.assigned_orders:
        if not course.ramassage_effectue:
            arrets.append(Arret(course.pickup_position, course.order_id, est_livraison=False))
        arrets.append(Arret(course.delivery_position, course.order_id, est_livraison=True))

    # Le retour au dépôt fait partie de sa journée : une course sur le chemin ne
    # lui coûte presque rien, une course à l'opposé le fait dévier pour rien.
    # Tant qu'aucun dépôt n'est renseigné, on n'invente pas ce trajet.
    if coursier.retour_depot is not None:
        arrets.append(Arret(coursier.retour_depot, "RETOUR-DEPOT", est_livraison=True))

    return arrets


def motif_inegibilite(coursier: Coursier, order: Order) -> Optional[str]:
    """
    Retourne la raison pour laquelle un coursier ne peut PAS prendre cette commande,
    ou None s'il est éligible.

    Règles :
    1. Coursier actif.
    2. Colis Voiture → fourgon ou voiture uniquement (trop volumineux pour scooter).
    3. La zone de livraison doit être dans les zones autorisées du véhicule.
    4. La charge actuelle + poids du colis ne doit pas dépasser la capacité max.

    Le motif est rédigé en clair : il est affiché tel quel au dispatcheur dans le
    mode pilote, pour qu'il comprenne pourquoi un coursier a été écarté.
    """
    # Règle 1 : actif
    if not coursier.is_active:
        return "Hors service"

    # Règle 2 : colis Voiture — réservé aux véhicules adaptés
    if order.volume_type == VolumeType.VOITURE:
        if coursier.vehicle_type not in (VehicleType.FOURGON, VehicleType.VOITURE):
            return "Colis trop encombrant pour un deux-roues : voiture ou fourgon"

    # Règle 3 : zone, élargie par l'autonomie du coursier.
    # La Grande Couronne n'est pas fermée aux scooters : elle est ouverte à ceux
    # qui emportent des batteries de rechange.
    zones_couvertes = list(ELIGIBLE_ZONES_BY_VEHICLE[coursier.vehicle_type])
    if coursier.autonomie_etendue and coursier.vehicle_type in VEHICULES_AUTONOMIE_ETENDUE_POSSIBLE:
        if Zone.GRANDE_COURONNE not in zones_couvertes:
            zones_couvertes.append(Zone.GRANDE_COURONNE)

    if order.zone not in zones_couvertes:
        zones = ", ".join(z.value.replace("_", " ") for z in zones_couvertes)
        if order.zone == Zone.GRANDE_COURONNE and coursier.vehicle_type in VEHICULES_AUTONOMIE_ETENDUE_POSSIBLE:
            return "Grande Couronne : pas assez d'autonomie (aucune batterie de rechange)"
        return f"Zone {order.zone.value.replace('_', ' ')} hors périmètre (couvre : {zones})"

    # Règle 4 : le destinataire accepte-t-il encore une livraison ?
    # Une entreprise qui ferme à midi ne se livre pas à 14h, quel que soit le
    # coursier — c'est un filtre, pas une pénalité.
    hors_plage = _hors_plage_de_livraison(order)
    if hors_plage:
        return hors_plage

    # Règle 5 : capacité
    order_weight = VOLUME_WEIGHTS[order.volume_type]
    if coursier.current_load + order_weight > MAX_LOAD_BY_VEHICLE[coursier.vehicle_type]:
        return (
            f"Capacité insuffisante : {coursier.current_load}/{MAX_LOAD_BY_VEHICLE[coursier.vehicle_type]} "
            f"utilisées, ce colis en demande {order_weight}"
        )

    return None


def _hors_plage_de_livraison(order: Order, maintenant: Optional[datetime] = None) -> Optional[str]:
    """
    Vérifie que le destinataire accepte encore une livraison aujourd'hui.

    Tant qu'aucune plage n'est renseignée, la règle dort : on ne devine pas des
    horaires d'ouverture qu'on ne connaît pas.
    """
    if order.livraison_ouverture is None and order.livraison_fermeture is None:
        return None

    maintenant = maintenant or datetime.now()
    heure = maintenant.strftime("%H:%M")

    if order.livraison_fermeture and heure > order.livraison_fermeture:
        return f"Destinataire fermé (livrable jusqu'à {order.livraison_fermeture})"
    if order.livraison_ouverture and heure < order.livraison_ouverture:
        return f"Destinataire pas encore ouvert (à partir de {order.livraison_ouverture})"
    return None


def is_coursier_eligible(coursier: Coursier, order: Order) -> bool:
    """Vrai si le coursier peut légalement prendre cette commande (cf. motif_inegibilite)."""
    return motif_inegibilite(coursier, order) is None


# ---------------------------------------------------------------------------
# Pénalité véhicule sous-optimal
# ---------------------------------------------------------------------------

def _vehicle_sub_optimal_penalty(
    coursier: Coursier,
    order: Order,
    trip_km: float,
    penalty_factor: float,
) -> float:
    """
    Calcule la pénalité (km équivalents) pour un véhicule non-idéal sur cette course.

    Ces pénalités n'excluent pas le coursier — elles le défavorisent simplement
    face à un véhicule plus adapté. Si aucun meilleur candidat n'est disponible,
    il sera quand même sélectionné.

    Args:
        coursier        : Coursier évalué.
        order          : Commande à attribuer.
        trip_km        : Distance ramassage → livraison (pré-calculée).
        penalty_factor : Multiplicateur (réduit à 40 % pour les clients Premium).
    """
    vtype   = coursier.vehicle_type
    penalty = 0.0

    # Un 50 en Petite Couronne : accepté, mais on y préfère un 125.
    # Pénalité légère — s'il est le mieux placé, il y va quand même.
    if vtype == VehicleType.SCOOT_50 and order.zone == Zone.PETITE_COURONNE:
        penalty += SCOOT_50_EN_PETITE_COURONNE_PENALITE_KM

    # Voiture sur trajet court : un scooter passe mieux dans Paris.
    if vtype == VehicleType.VOITURE and trip_km < LONG_TRIP_MIN_KM:
        penalty += VOITURE_SHORT_TRIP_PENALTY_KM

    # fourgon sur petit volume + court trajet
    # → privilégier un scooter, moins coûteux et plus manœuvrable
    if (
        vtype == VehicleType.FOURGON
        and order.volume_type != VolumeType.VOITURE
        and trip_km < FOURGON_SMALL_TRIP_MAX_KM
    ):
        penalty += FOURGON_SMALL_TRIP_PENALTY_KM

    return penalty * penalty_factor


def urgences_a_bord(coursier: Coursier, fleet: "FleetManager | None") -> int:
    """
    Nombre de courses pressées que le coursier transporte en ce moment.

    « Pressée » se juge au temps qu'il RESTE, pas au délai annoncé : une course
    promise en trois heures dont il ne reste que dix minutes est une urgence,
    une course promise en une heure qui vient d'arriver ne l'est pas encore.
    """
    if fleet is None:
        return 0
    compte = 0
    for embarquee in coursier.assigned_orders:
        commande = fleet.get_order(embarquee.order_id)
        if commande is None or commande.deadline_minutes is None:
            continue
        ecoule = (datetime.now() - commande.created_at).total_seconds() / 60.0
        if commande.deadline_minutes - ecoule <= SEUIL_URGENCE_MINUTES:
            compte += 1
    return compte


def course_pressee(order: Order) -> bool:
    """Vrai si la course à attribuer est elle-même une urgence."""
    if order.deadline_minutes is None:
        return False
    ecoule = (datetime.now() - order.created_at).total_seconds() / 60.0
    return order.deadline_minutes - ecoule <= SEUIL_URGENCE_MINUTES


def avis_de_validation(
    coursier: Coursier,
    detour_km: float,
    maintenant: Optional[datetime] = None,
) -> tuple[bool, Optional[str]]:
    """
    Dit si le moteur doit rendre la main au dispatcheur plutôt que trancher.

    Deux situations où décider à la place du coursier serait déplacé :

    - **il termine bientôt.** Lui annoncer qu'il n'a pas fini sa journée n'est
      pas une décision d'algorithme. Le dispatcheur négocie, puis tranche.
    - **la course dévie son trajet retour.** Un crochet sur la route du dépôt ne
      pose aucun problème ; au-delà, on lui demande.

    Le meilleur candidat reste désigné dans les deux cas — c'est un avis joint
    au classement, pas un refus.
    """
    fin = coursier.fin_service
    if fin is not None:
        restant = (fin - (maintenant or datetime.now())).total_seconds() / 60.0
        if restant <= SEUIL_VALIDATION_FIN_SERVICE_MINUTES:
            return True, f"il termine dans {max(0, int(restant))} min — à voir avec lui"

    if coursier.retour_depot is not None and detour_km > SEUIL_VALIDATION_DETOUR_RETOUR_KM:
        return True, f"le dévie de {detour_km:.1f} km sur son trajet retour — à voir avec lui"

    return False, None


def penalite_debordement_horaire(
    coursier: Coursier,
    order: Order,
    maintenant: Optional[datetime] = None,
) -> tuple[float, Optional[str]]:
    """
    Ce que la course déborderait sur sa pause ou sa fin de service.

    Un coursier qui part manger dans cinq minutes ne prend pas une course d'un
    quart d'heure : elle finirait au bureau, ou pas du tout. Tant que ses
    horaires ne sont pas renseignés, la règle ne s'applique pas — on ne devine
    pas des contraintes qu'on ne connaît pas.

    Returns:
        (pénalité en km équivalents, motif lisible).
    """
    arret = coursier.prochain_arret
    if arret is None:
        return 0.0, None

    maintenant = maintenant or datetime.now()
    disponible = (arret - maintenant).total_seconds() / 60.0 - MARGE_AVANT_ARRET_MINUTES

    vitesse = VITESSE_MOYENNE_KMH[coursier.vehicle_type]
    nouveaux = [
        Arret(GpsPosition(lat=order.pickup.lat, lon=order.pickup.lon), order.id, est_livraison=False),
        Arret(GpsPosition(lat=order.delivery.lat, lon=order.delivery.lon), order.id, est_livraison=True),
    ]
    tous = arrets_en_cours(coursier) + nouveaux
    _, km = ordonner_tournee(position_effective(coursier, maintenant), tous)
    necessaire = minutes_pour_parcourir(km, vitesse, len(tous)) + order.minutes_attente

    debordement = necessaire - disponible
    if debordement <= 0:
        return 0.0, None

    motif = "déborderait sur sa pause" if coursier.debut_pause == arret else "déborderait sur sa fin de service"
    return debordement * PENALITE_RETARD_PAR_MINUTE, motif


def penalite_retard_induit(
    coursier: Coursier,
    order: Order,
    fleet: "FleetManager | None" = None,
) -> tuple[float, Optional[str]]:
    """
    Ce que la nouvelle course ferait perdre aux livraisons déjà promises.

    Un coursier attendu à 15h30 alors qu'il est 14h47 ne doit pas être chargé
    d'autre chose : sa course urgente passe avant l'optimisation du kilométrage.
    On compare donc, pour chaque livraison à échéance qu'il transporte, le temps
    qu'il lui reste au temps qu'il lui faudra une fois la nouvelle course insérée.

    Une course dont le ramassage tombe pile sur sa route ne le retarde
    pratiquement pas : elle reste acceptable, exactement comme sur le terrain où
    l'on prend au passage quitte à confier la livraison à un autre.

    Returns:
        (pénalité en km équivalents, motif lisible si la pénalité est notable).
    """
    echeances = _echeances_embarquees(coursier, fleet)
    if not echeances:
        return 0.0, None

    position = position_effective(coursier)
    vitesse  = VITESSE_MOYENNE_KMH[coursier.vehicle_type]

    arrets_actuels = arrets_en_cours(coursier)
    nouveaux = [
        Arret(GpsPosition(lat=order.pickup.lat, lon=order.pickup.lon), order.id, est_livraison=False),
        Arret(GpsPosition(lat=order.delivery.lat, lon=order.delivery.lon), order.id, est_livraison=True),
    ]

    avant = _minutes_avant_livraison(position, arrets_actuels, vitesse)
    apres = _minutes_avant_livraison(position, arrets_actuels + nouveaux, vitesse)

    penalite = 0.0
    plus_serre: Optional[str] = None
    marge_min = float("inf")

    for order_id, minutes_restantes in echeances.items():
        if order_id not in avant or order_id not in apres:
            continue
        retard = apres[order_id] - avant[order_id]
        marge  = minutes_restantes - apres[order_id] - MARGE_SECURITE_MINUTES
        if retard > 0 and marge < 0:
            # Le retard ne coûte que s'il mord sur une marge déjà insuffisante.
            depassement = min(retard, -marge)
            penalite += depassement * PENALITE_RETARD_PAR_MINUTE
            if marge < marge_min:
                marge_min = marge
                plus_serre = order_id

    motif = None
    if plus_serre is not None:
        motif = f"mettrait en retard sa livraison {plus_serre}"
    return penalite, motif


def _echeances_embarquees(coursier: Coursier, fleet: "FleetManager | None") -> dict[str, float]:
    """Minutes restantes avant échéance, pour chaque course embarquée qui en a une."""
    if fleet is None:
        return {}
    restantes: dict[str, float] = {}
    for embarquee in coursier.assigned_orders:
        commande = fleet.get_order(embarquee.order_id)
        if commande is None or commande.deadline_minutes is None:
            continue
        ecoule = (datetime.now() - commande.created_at).total_seconds() / 60.0
        restantes[embarquee.order_id] = commande.deadline_minutes - ecoule
    return restantes


def minutes_pour_parcourir(km: float, vitesse_kmh: float, nb_arrets: int) -> float:
    """
    Temps réel d'une tournée : le trajet majoré, plus le temps passé sur place.

    Deux choses que la carte ne montre pas. Un itinéraire annoncé à 24 minutes
    n'en fait jamais 24 — rue barrée, double file, manifestation. Et à chaque
    arrêt il faut trouver la porte, monter, attendre à l'accueil, faire signer.
    Les ignorer sous-estimait chaque tournée d'autant.
    """
    return km / vitesse_kmh * 60.0 * MARGE_TRAJET + nb_arrets * MINUTES_PAR_ARRET


def _minutes_avant_livraison(
    depart: GpsPosition,
    arrets: List[Arret],
    vitesse_kmh: float,
) -> dict[str, float]:
    """Minutes écoulées avant d'atteindre chaque livraison, sur l'itinéraire optimal."""
    ordre, _ = ordonner_tournee(depart, arrets)
    minutes: dict[str, float] = {}
    position = depart
    cumul_km = 0.0
    for rang, arret in enumerate(ordre, start=1):
        cumul_km += haversine(position, arret.position)
        position = arret.position
        if arret.est_livraison:
            minutes[arret.course_id] = minutes_pour_parcourir(cumul_km, vitesse_kmh, rang)
    return minutes


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score_coursier(coursier: Coursier, order: Order, fleet: "FleetManager | None" = None) -> float:
    """
    Calcule le score d'un coursier pour une commande. Plus bas = meilleur.

    Composantes :

    1. distance_base (km)
       Distance Haversine entre la position actuelle du coursier et le ramassage.
       Facteur dominant du score.

    2. pénalité_charge (km équivalents)
       = charge_actuelle × LOAD_PENALTY_PER_UNIT × facteur_urgence
       Équilibre la flotte. Réduite à mesure que l'urgence augmente
       (un coursier chargé vaut mieux qu'un coursier lointain si c'est urgent).

    3. pénalité_véhicule (km équivalents)
       Pénalise les véhicules non-idéaux pour cette course.
       Réduite à 40 % pour les clients Premium.

    4. bonus_groupage (km équivalents, soustrait)
       Si le ramassage est proche du trajet actuel du coursier, il est déjà dans
       le quartier → priorité forte pour éviter les croisements de trajets.
       Désactivé si urgence > URGENCY_GROUPAGE_DISABLE_THRESHOLD.

    Args:
        coursier : Coursier éligible à évaluer.
        order   : Commande à attribuer.

    Returns:
        Score en kilomètres équivalents. Plus bas = plus prioritaire.
        **Peut être négatif** : la course raccourcit alors sa tournée.
    """
    return score_detail(coursier, order, fleet).total


@dataclass
class ScoreDetail:
    """
    Décomposition d'un score, poste par poste.

    Le mode pilote affiche cette décomposition au dispatcheur : sans elle, le
    moteur n'est qu'un nombre à croire sur parole. Avec elle, il devient
    discutable — donc réglable.

    Tous les postes sont en kilomètres (ou km équivalents pour les pénalités).
    """
    total: float
    detour_km: float          # kilomètres ajoutés à sa tournée — poste dominant
    distance_km: float        # coursier → ramassage, à titre indicatif
    penalite_charge: float    # dissuade de surcharger un coursier
    penalite_vehicule: float  # véhicule non idéal pour cette course
    penalite_retard: float    # retard infligé à une livraison déjà promise
    motif_retard: Optional[str]
    urgences_portees: int     # combien de courses pressées il transporte déjà
    penalite_cumul_urgences: float
    penalite_debordement: float   # déborde sur sa pause ou sa fin de service
    motif_debordement: Optional[str]
    validation_requise: bool      # le moteur propose, le dispatcheur tranche
    motif_validation: Optional[str]
    trajet_km: float          # ramassage → livraison
    urgence: float            # 0.0 → 1.0

    def explications(self) -> list[str]:
        """Postes non nuls, formulés en clair pour l'interface."""
        if self.detour_km < -0.05:
            lignes = [f"sur sa tournée — raccourcit de {abs(self.detour_km):.1f} km"]
        elif self.detour_km < self.distance_km - 0.05:
            lignes = [f"détour de {self.detour_km:.1f} km (ramassage à {self.distance_km:.1f} km)"]
        else:
            lignes = [f"détour de {self.detour_km:.1f} km"]
        if self.penalite_charge > 0.01:
            lignes.append(f"+{self.penalite_charge:.1f} de pénalité de charge")
        if self.penalite_vehicule > 0.01:
            lignes.append(f"+{self.penalite_vehicule:.1f} véhicule non idéal")
        if self.penalite_retard > 0.01 and self.motif_retard:
            lignes.append(f"+{self.penalite_retard:.1f} {self.motif_retard}")
        if self.penalite_debordement > 0.01 and self.motif_debordement:
            lignes.append(f"+{self.penalite_debordement:.1f} {self.motif_debordement}")
        if self.validation_requise and self.motif_validation:
            lignes.append(f"⚠ {self.motif_validation}")
        if self.penalite_cumul_urgences > 0.01:
            lignes.append(f"+{self.penalite_cumul_urgences:.0f} il court déjà après une urgence")
        elif self.urgences_portees == 0:
            lignes.append("libre de toute échéance : un crochet lui coûte moins")
        return lignes


def score_detail(coursier: Coursier, order: Order, fleet: "FleetManager | None" = None) -> ScoreDetail:
    """
    Calcule le score ET sa décomposition (cf. score_coursier pour les composantes).

    Returns:
        ScoreDetail — `total` est le score final, les autres champs sont les postes.
    """
    pickup_pos   = GpsPosition(lat=order.pickup.lat,   lon=order.pickup.lon)
    delivery_pos = GpsPosition(lat=order.delivery.lat, lon=order.delivery.lon)

    urgency        = order.urgency_score                              # 0.0 → 1.0
    penalty_factor = PREMIUM_PENALTY_FACTOR if order.is_premium else 1.0
    trip_km        = haversine(pickup_pos, delivery_pos)

    # Position EXPLOITABLE (GPS récent ou estimée) : noter un coursier sur une
    # position d'il y a une heure reviendrait à dispatcher à l'aveugle.
    position = position_effective(coursier)

    # 1. Détour marginal — le poste dominant.
    # Ce n'est pas la distance au ramassage qui décide, c'est ce que la course
    # rallonge à la tournée en cours. Une course dont le ramassage est à 200 m
    # mais dont la livraison fait repartir en arrière coûte plus cher qu'une
    # course à 1 km dont la livraison est sur la route. Valeur négative quand la
    # course recouvre une portion de trajet déjà prévue.
    detour = detour_marginal(position, arrets_en_cours(coursier), pickup_pos, delivery_pos, order.id)

    # Conservée à titre indicatif : le dispatcheur raisonne d'abord en distance.
    base_distance = haversine(position, pickup_pos)

    # 2. Pénalité de charge — allégée linéairement avec l'urgence
    load_factor  = max(URGENCY_LOAD_PENALTY_MIN_FACTOR, 1.0 - urgency)
    load_penalty = coursier.current_load * LOAD_PENALTY_PER_UNIT * load_factor

    # 3. Pénalité véhicule sous-optimal
    vehicle_penalty = _vehicle_sub_optimal_penalty(coursier, order, trip_km, penalty_factor)

    # 4. Retard infligé aux livraisons déjà promises.
    # Le kilométrage n'est pas le seul coût : faire rater une livraison annoncée
    # coûte bien davantage que le détour qui l'a provoquée.
    retard_penalty, motif_retard = penalite_retard_induit(coursier, order, fleet)

    # 5. Ce qu'il porte décide de ce qu'il peut accepter.
    # Un coursier libre de toute échéance se détourne volontiers, même de loin.
    # Un coursier qui court déjà après une urgence ne doit pas en recevoir une
    # seconde : elles se feraient rater l'une l'autre.
    # 6. Sa pause ou sa fin de service.
    debordement, motif_debordement = penalite_debordement_horaire(coursier, order)

    pressees = urgences_a_bord(coursier, fleet)
    if pressees == 0:
        detour *= FACTEUR_DETOUR_SANS_URGENCE
    penalite_cumul = PENALITE_URGENCES_CUMULEES_KM if (pressees and course_pressee(order)) else 0.0

    # 7. Le moteur doit-il trancher, ou rendre la main ?
    # Calculé sur le détour final : le motif affiché doit annoncer le même
    # nombre de kilomètres que celui que le dispatcheur lit dans le classement.
    validation, motif_validation = avis_de_validation(coursier, detour)

    # Pas de plancher. Un score négatif signifie que la course fait GAGNER du
    # chemin au coursier, et c'est une information — l'écraser à 0,01 mettait à
    # égalité deux excellents candidats que la charge séparait pourtant
    # nettement, et rendait le classement arbitraire là où il devait être le
    # plus fin.
    total = (detour + load_penalty + vehicle_penalty + retard_penalty
             + penalite_cumul + debordement)

    return ScoreDetail(
        total=total,
        detour_km=detour,
        distance_km=base_distance,
        penalite_charge=load_penalty,
        penalite_vehicule=vehicle_penalty,
        penalite_retard=retard_penalty,
        motif_retard=motif_retard,
        urgences_portees=pressees,
        penalite_cumul_urgences=penalite_cumul,
        penalite_debordement=debordement,
        motif_debordement=motif_debordement,
        validation_requise=validation,
        motif_validation=motif_validation,
        trajet_km=trip_km,
        urgence=urgency,
    )


# ---------------------------------------------------------------------------
# Sélection du meilleur coursier
# ---------------------------------------------------------------------------

def get_coursiers_eligibles(order: Order, fleet: FleetManager) -> List[Coursier]:
    """Retourne tous les coursiers actifs éligibles pour cette commande."""
    return [c for c in fleet.get_active_coursiers() if is_coursier_eligible(c, order)]


def find_best_coursier(order: Order, fleet: FleetManager) -> Optional[tuple[Coursier, float]]:
    """
    Évalue tous les coursiers éligibles et retourne le meilleur.

    Returns:
        (meilleur_coursier, score) ou None si aucun éligible.
    """
    eligible = get_coursiers_eligibles(order, fleet)
    if not eligible:
        return None

    scored = [(score_coursier(c, order, fleet), c) for c in eligible]
    scored.sort(key=lambda x: x[0])
    best_score, best_coursier = scored[0]
    return best_coursier, best_score


# ---------------------------------------------------------------------------
# Point d'entrée principal
# ---------------------------------------------------------------------------

def dispatch_order(order: Order, fleet: FleetManager) -> DispatchResult:
    """
    Lance le pipeline complet de dispatch pour une commande.

    1. Compte les éligibles
    2. Cherche le meilleur
    3. Attribue via FleetManager
    4. Retourne un DispatchResult détaillé

    Args:
        order : Commande avec statut PENDING.
        fleet : État courant de la flotte (lu + modifié).
    """
    eligible_count = len(get_coursiers_eligibles(order, fleet))
    result         = find_best_coursier(order, fleet)

    if result is None:
        order.status = OrderStatus.UNASSIGNABLE
        urgency_hint = f" (urgence : {order.urgency_score:.0%})" if order.deadline_minutes else ""
        tier_hint    = " [PREMIUM]" if order.is_premium else ""
        return DispatchResult(
            success=False,
            order_id=order.id,
            assigned_to=None,
            score=None,
            reason=(
                f"Aucun coursier éligible{tier_hint} pour la zone «{order.zone}»"
                f" avec le volume «{order.volume_type}»{urgency_hint}."
            ),
            eligible_count=eligible_count,
        )

    best_coursier, best_score = result
    fleet.assign_order_to_coursier(order, best_coursier.code)

    urgency_label = f" | urgence {order.urgency_score:.0%}" if order.deadline_minutes else ""
    tier_label    = " [PREMIUM]" if order.is_premium else ""

    return DispatchResult(
        success=True,
        order_id=order.id,
        assigned_to=best_coursier.code,
        score=round(best_score, 3),
        reason=(
            f"Coursier «{best_coursier.code}» assigné{tier_label}"
            f" — score {best_score:.2f} km"
            f" | charge {best_coursier.current_load}/{best_coursier.max_load}"
            f"{urgency_label}."
        ),
        eligible_count=eligible_count,
    )
