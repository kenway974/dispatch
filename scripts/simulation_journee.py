#!/usr/bin/env python3
"""
Simule une journée complète d'exploitation et fait trancher le moteur à chaque course.

Sert à deux choses :

1. **Voir le moteur travailler sous charge réelle.** Une centaine de courses sur
   onze heures, huit coursiers qui prennent leur service en décalé, se chargent,
   livrent, partent en pause et rentrent. Un moteur qui se comporte bien sur trois
   courses isolées peut très mal vieillir sur une journée entière — saturer les
   mêmes coursiers, en oublier d'autres, s'effondrer aux heures de pointe.

2. **Produire des cas d'école véridiques.** Chaque décision est enregistrée avec
   son classement complet et la décomposition du score. Les exemples qui servent
   à expliquer les règles sortent donc du moteur, ils ne sont pas rédigés à la main.

Usage :
    python scripts/simulation_journee.py            # résumé lisible
    python scripts/simulation_journee.py --json     # journal complet
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.config import VITESSE_MOYENNE_KMH
from app.models.coursier import AssignedOrder, Coursier, GpsPosition
from app.models.enums import ClientTier, VehicleType, VolumeType, Zone
from app.models.order import Coordinates, Order
from app.services.comparaison import classer_coursiers
from app.services.dispatch import arrets_en_cours
from app.services.fleet import FleetManager
from app.services.geo import haversine, ordonner_tournee

OUVERTURE = 8 * 60 + 30      # 08:30, en minutes depuis minuit
FERMETURE = 19 * 60 + 30     # 19:30


def hhmm(minutes: float) -> str:
    return f"{int(minutes) // 60:02d}:{int(minutes) % 60:02d}"


# ---------------------------------------------------------------------------
# Le terrain : adresses réelles, clients récurrents
# ---------------------------------------------------------------------------

LIEUX: dict[str, tuple[float, float, Zone]] = {
    # Paris
    "Bureau Lungta (17e)":        (48.8838, 2.3243, Zone.PARIS),
    "Bastille (11e)":             (48.8533, 2.3692, Zone.PARIS),
    "Montmartre (18e)":           (48.8864, 2.3432, Zone.PARIS),
    "Montparnasse (14e)":         (48.8422, 2.3220, Zone.PARIS),
    "République (3e)":            (48.8675, 2.3636, Zone.PARIS),
    "Concorde (8e)":              (48.8656, 2.3212, Zone.PARIS),
    "Nation (12e)":               (48.8483, 2.3958, Zone.PARIS),
    "Gare du Nord (10e)":         (48.8809, 2.3553, Zone.PARIS),
    "Gare de Lyon (12e)":         (48.8443, 2.3743, Zone.PARIS),
    "Opéra (9e)":                 (48.8709, 2.3317, Zone.PARIS),
    "Rue de la Boétie (8e)":      (48.8712, 2.3092, Zone.PARIS),
    "Avenue Foch (16e)":          (48.8710, 2.2830, Zone.PARIS),
    "Trocadéro (16e)":            (48.8629, 2.2875, Zone.PARIS),
    "Bercy (12e)":                (48.8400, 2.3830, Zone.PARIS),
    "Belleville (20e)":           (48.8722, 2.3767, Zone.PARIS),
    "Invalides (7e)":             (48.8566, 2.3125, Zone.PARIS),
    "Châtelet (1er)":             (48.8583, 2.3470, Zone.PARIS),
    "Batignolles (17e)":          (48.8877, 2.3170, Zone.PARIS),
    "Place d'Italie (13e)":       (48.8312, 2.3555, Zone.PARIS),
    "Porte de Versailles (15e)":  (48.8322, 2.2875, Zone.PARIS),
    # Petite Couronne
    "Levallois (92)":             (48.8939, 2.2874, Zone.PETITE_COURONNE),
    "Boulogne (92)":              (48.8365, 2.2400, Zone.PETITE_COURONNE),
    "Saint-Denis (93)":           (48.9360, 2.3553, Zone.PETITE_COURONNE),
    "Pantin (93)":                (48.8944, 2.4090, Zone.PETITE_COURONNE),
    "Montreuil (93)":             (48.8638, 2.4485, Zone.PETITE_COURONNE),
    "Créteil (94)":               (48.7773, 2.4555, Zone.PETITE_COURONNE),
    "Vitry (94)":                 (48.7875, 2.3928, Zone.PETITE_COURONNE),
    "Issy (92)":                  (48.8244, 2.2730, Zone.PETITE_COURONNE),
    # Grande Couronne
    "Versailles (78)":            (48.8045, 2.1200, Zone.GRANDE_COURONNE),
    "Roissy CDG (95)":            (49.0097, 2.5479, Zone.GRANDE_COURONNE),
    "Orly (91)":                  (48.7262, 2.3652, Zone.GRANDE_COURONNE),
    "Cergy (95)":                 (49.0350, 2.0600, Zone.GRANDE_COURONNE),
    "Marne-la-Vallée (77)":       (48.8420, 2.7870, Zone.GRANDE_COURONNE),
}


@dataclass(frozen=True)
class Client:
    nom: str
    adresse: str
    premium: bool = False


CLIENTS = [
    Client("Cabinet Vermeulen",   "Rue de la Boétie (8e)",     premium=True),
    Client("Laboratoire Astier",  "Porte de Versailles (15e)", premium=True),
    Client("Clinique du Parc",    "Avenue Foch (16e)",         premium=True),
    Client("Studio Perrin",       "Châtelet (1er)"),
    Client("Maison Farel",        "Opéra (9e)"),
    Client("Comptoir Grangé",     "Bastille (11e)"),
    Client("Éditions Sauvin",     "Nation (12e)"),
    Client("Atelier Bonnaire",    "Batignolles (17e)"),
    Client("Groupe Delcourt",     "Levallois (92)"),
    Client("Pharmacie Vasseur",   "Place d'Italie (13e)"),
]

# Destinations plausibles, pondérées : l'essentiel reste dans Paris.
DESTINATIONS = (
    [n for n, (_, _, z) in LIEUX.items() if z == Zone.PARIS] * 5
    + [n for n, (_, _, z) in LIEUX.items() if z == Zone.PETITE_COURONNE] * 2
    + [n for n, (_, _, z) in LIEUX.items() if z == Zone.GRANDE_COURONNE]
)


# ---------------------------------------------------------------------------
# La flotte : services décalés, pauses variables
# ---------------------------------------------------------------------------

@dataclass
class Service:
    code: str
    vehicule: VehicleType
    base: str
    autonomie_etendue: bool = False
    debut: int = 0       # minutes depuis minuit
    fin: int = 0
    pause: tuple[int, int] | None = None   # (début, fin) ou None si le coursier n'en prend pas

    def en_service(self, t: int) -> bool:
        if not (self.debut <= t < self.fin):
            return False
        if self.pause and self.pause[0] <= t < self.pause[1]:
            return False
        return True


SERVICES = [
    # 8 h de travail chacun, dans l'amplitude 08:30 – 19:30.
    # Deux 125 emportent des batteries de rechange : eux seuls font la Grande Couronne.
    Service("KEN", VehicleType.SCOOT_50,  "Bastille (11e)",       False,  8 * 60 + 30, 17 * 60 + 30, (12 * 60 + 30, 13 * 60 + 30)),
    Service("MEH", VehicleType.SCOOT_50,  "Montmartre (18e)",     False,  8 * 60 + 30, 17 * 60 + 30, (13 * 60, 14 * 60)),
    Service("LIM", VehicleType.SCOOT_50,  "Montparnasse (14e)",   False,  9 * 60,      17 * 60,      None),
    Service("MIC", VehicleType.SCOOT_125, "Bureau Lungta (17e)",  False,  9 * 60 + 30, 18 * 60 + 30, (13 * 60 + 30, 14 * 60 + 30)),
    Service("JC",  VehicleType.SCOOT_125, "Saint-Denis (93)",     True,   8 * 60 + 30, 17 * 60 + 30, (12 * 60, 13 * 60)),
    Service("MEF", VehicleType.SCOOT_125, "Créteil (94)",         True,  10 * 60 + 30, 19 * 60 + 30, (14 * 60, 15 * 60)),
    Service("LAH", VehicleType.FOURGON,   "Vitry (94)",           False,  9 * 60,      18 * 60,      (13 * 60, 14 * 60)),
    Service("SET", VehicleType.VOITURE,   "Orly (91)",            False, 10 * 60 + 30, 18 * 60 + 30, None),
]


def position(nom: str) -> GpsPosition:
    lat, lon, _ = LIEUX[nom]
    return GpsPosition(lat=lat, lon=lon)


# ---------------------------------------------------------------------------
# Génération de la journée
# ---------------------------------------------------------------------------

def profil_arrivees(nb: int, alea: random.Random) -> list[int]:
    """
    Horaires d'arrivée des courses, avec deux pointes.

    Une boîte de course ne reçoit pas ses demandes uniformément : ça déborde en
    fin de matinée et en milieu d'après-midi, c'est creux à midi. Simuler une
    arrivée plate donnerait un moteur qui n'a jamais vu de pointe.
    """
    horaires: list[int] = []
    # Les courses cessent d'arriver une heure avant la fermeture : au-delà,
    # plus personne ne peut raisonnablement livrer avant 19h30.
    dernier = FERMETURE - 60
    while len(horaires) < nb:
        pic = alea.choice([10 * 60 + 30, 10 * 60 + 30, 15 * 60 + 30, 15 * 60 + 30, 13 * 60])
        t = int(alea.gauss(pic, 95))
        if OUVERTURE <= t <= dernier:
            horaires.append(t)
    return sorted(horaires)


def fabriquer_courses(nb: int, alea: random.Random) -> list[dict]:
    """Construit la journée : qui commande quoi, vers où, avec quelle urgence."""
    courses = []
    for i, arrivee in enumerate(profil_arrivees(nb, alea), start=1):
        client = alea.choices(CLIENTS, weights=[5, 4, 4, 3, 3, 3, 2, 2, 2, 2])[0]

        # Un retour part d'un point quelconque et revient au bureau.
        est_retour = alea.random() < 0.12
        if est_retour:
            ramassage = alea.choice(DESTINATIONS)
            livraison = "Bureau Lungta (17e)"
        else:
            ramassage = client.adresse
            livraison = alea.choice(DESTINATIONS)
            while livraison == ramassage:
                livraison = alea.choice(DESTINATIONS)

        volume = alea.choices(
            [VolumeType.STANDARD, VolumeType.VOLUME, VolumeType.VOITURE],
            weights=[78, 18, 4],
        )[0]

        # Les clients premium demandent plus souvent des délais courts.
        if est_retour:
            delai = None                                   # un retour attend
        elif client.premium:
            delai = alea.choices([30, 60, 90, 180, None], weights=[18, 30, 27, 20, 5])[0]
        else:
            delai = alea.choices([30, 60, 90, 180, None], weights=[5, 18, 30, 32, 15])[0]

        courses.append({
            "id": f"C{i:03d}",
            "arrivee": arrivee,
            "client": client,
            "ramassage": ramassage,
            "livraison": livraison,
            "zone": LIEUX[livraison][2],
            "volume": volume,
            "delai": delai,
            "retour": est_retour,
        })
    return courses


# ---------------------------------------------------------------------------
# Déroulé de la journée
# ---------------------------------------------------------------------------

@dataclass
class Etat:
    """Ce que la simulation suit en plus de la flotte : horaires prévus, historique."""
    fleet: FleetManager
    fin_prevue: dict[str, float] = field(default_factory=dict)   # order_id → minute de livraison estimée
    journal: list[dict] = field(default_factory=list)
    livrees: int = 0
    non_attribuees: list[dict] = field(default_factory=list)


def replanifier(etat: Etat, coursier: Coursier, maintenant: int) -> None:
    """Recalcule l'heure de livraison prévue de chaque course du portefeuille."""
    ordre, _ = ordonner_tournee(coursier.position, arrets_en_cours(coursier))
    vitesse = VITESSE_MOYENNE_KMH[coursier.vehicle_type]
    point = coursier.position
    cumul = 0.0
    for arret in ordre:
        cumul += haversine(point, arret.position)
        point = arret.position
        if arret.est_livraison:
            etat.fin_prevue[arret.course_id] = maintenant + cumul / vitesse * 60.0


def avancer(etat: Etat, maintenant: int) -> None:
    """
    Fait vivre la flotte jusqu'à l'instant donné.

    Les courses dont l'heure de livraison prévue est passée sont clôturées, le
    coursier est déplacé au dernier point livré, et les services et pauses sont
    appliqués. Sans ça, tout le monde serait saturé avant midi et le moteur
    n'aurait plus personne à qui donner quoi que ce soit.
    """
    for service in SERVICES:
        coursier = etat.fleet.get_coursier(service.code)
        if coursier is None:
            continue

        for embarquee in list(coursier.assigned_orders):
            fin = etat.fin_prevue.get(embarquee.order_id)
            if fin is not None and fin <= maintenant:
                etat.fleet.update_coursier_position(
                    coursier.code, embarquee.delivery_lat, embarquee.delivery_lon
                )
                etat.fleet.remove_order_from_coursier(embarquee.order_id, coursier.code)
                etat.fin_prevue.pop(embarquee.order_id, None)
                etat.livrees += 1

        etat.fleet.set_coursier_active(service.code, service.en_service(maintenant))


def simuler(nb_courses: int, graine: int) -> dict:
    alea = random.Random(graine)
    fleet = FleetManager()
    for service in SERVICES:
        fleet.add_coursier(Coursier(
            code=service.code,
            vehicle_type=service.vehicule,
            position=position(service.base),
            autonomie_etendue=service.autonomie_etendue,
            is_active=service.en_service(OUVERTURE),
        ))

    etat = Etat(fleet=fleet)
    courses = fabriquer_courses(nb_courses, alea)
    # Toutes les courses de la journée sont créées à leur heure d'arrivée réelle,
    # pour que le score d'urgence du moteur se calcule sur le bon temps écoulé.
    origine = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    for course in courses:
        maintenant = course["arrivee"]
        avancer(etat, maintenant)

        order = Order(
            id=course["id"],
            pickup=Coordinates(lat=LIEUX[course["ramassage"]][0], lon=LIEUX[course["ramassage"]][1]),
            delivery=Coordinates(lat=LIEUX[course["livraison"]][0], lon=LIEUX[course["livraison"]][1]),
            zone=course["zone"],
            volume_type=course["volume"],
            client_tier=ClientTier.PREMIUM if course["client"].premium else ClientTier.STANDARD,
            deadline_minutes=course["delai"],
            created_at=origine + timedelta(minutes=maintenant),
        )

        classement = classer_coursiers(order, fleet)
        eligibles = [c for c in classement if c.eligible]

        if not eligibles:
            etat.non_attribuees.append({**course, "motifs": [
                {"code": c.code, "motif": c.motif_inegibilite} for c in classement
            ]})
            continue

        retenu = eligibles[0]
        fleet.add_order(order)
        fleet.assign_order_to_coursier(order, retenu.code)
        replanifier(etat, fleet.get_coursier(retenu.code), maintenant)

        etat.journal.append({
            "id": course["id"],
            "heure": hhmm(maintenant),
            "minute": maintenant,
            "client": course["client"].nom,
            "premium": course["client"].premium,
            "ramassage": course["ramassage"],
            "livraison": course["livraison"],
            "zone": course["zone"].value,
            "volume": course["volume"].value,
            "delai": course["delai"],
            "retour": course["retour"],
            "retenu": retenu.code,
            "score": retenu.score,
            "ecart_second": round(eligibles[1].score - retenu.score, 2) if len(eligibles) > 1 else None,
            "nb_eligibles": len(eligibles),
            "nb_ecartes": len(classement) - len(eligibles),
            "classement": [c.to_dict() for c in classement],
        })

    # Clôture : on laisse la flotte finir ses livraisons jusqu'à la fermeture.
    avancer(etat, FERMETURE)

    return {
        "journal": etat.journal,
        "non_attribuees": etat.non_attribuees,
        "livrees": etat.livrees,
        "total": nb_courses,
    }


# ---------------------------------------------------------------------------
# Restitution
# ---------------------------------------------------------------------------

def resumer(resultat: dict) -> None:
    journal = resultat["journal"]
    print(f"\n{'═' * 74}")
    print(f"  JOURNÉE SIMULÉE — {resultat['total']} courses · 08:30 → 19:30 · 8 coursiers")
    print(f"{'═' * 74}\n")

    print(f"  Attribuées      {len(journal)}")
    print(f"  Sans coursier   {len(resultat['non_attribuees'])}")
    print(f"  Livrées         {resultat['livrees']}\n")

    par_coursier: dict[str, list[dict]] = {}
    for d in journal:
        par_coursier.setdefault(d["retenu"], []).append(d)

    print("  RÉPARTITION")
    for service in SERVICES:
        courses = par_coursier.get(service.code, [])
        barre = "█" * len(courses)
        print(f"   {service.code:4} {service.vehicule.value:22} {len(courses):3}  {barre}")

    print("\n  CHARGE PAR HEURE")
    par_heure: dict[int, int] = {}
    for d in journal:
        par_heure[d["minute"] // 60] = par_heure.get(d["minute"] // 60, 0) + 1
    for h in range(8, 20):
        n = par_heure.get(h, 0)
        print(f"   {h:02d}h {'▄' * n} {n if n else ''}")

    serres = [d for d in journal if d["ecart_second"] is not None and d["ecart_second"] < 0.3]
    print(f"\n  Décisions serrées (moins de 0,3 km d'écart avec le 2e) : {len(serres)}")
    print(f"  Courses premium : {sum(1 for d in journal if d['premium'])}")
    print(f"  Retours : {sum(1 for d in journal if d['retour'])}")

    if resultat["non_attribuees"]:
        print("\n  NON ATTRIBUÉES")
        for c in resultat["non_attribuees"][:8]:
            print(f"   {c['id']} {hhmm(c['arrivee'])} {c['ramassage'][:24]:24} → {c['livraison'][:24]:24} {c['volume'].value}")
    print()


def main() -> int:
    parseur = argparse.ArgumentParser(description=__doc__)
    parseur.add_argument("--courses", type=int, default=100)
    parseur.add_argument("--graine", type=int, default=7)
    parseur.add_argument("--json", action="store_true", help="journal complet en JSON")
    args = parseur.parse_args()

    resultat = simuler(args.courses, args.graine)
    if args.json:
        print(json.dumps(resultat, ensure_ascii=False, default=str))
    else:
        resumer(resultat)
    return 0


if __name__ == "__main__":
    sys.exit(main())
