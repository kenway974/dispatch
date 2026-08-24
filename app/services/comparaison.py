"""
Mode pilote — comparaison entre le dispatch manuel et le dispatch automatique.

Principe de l'essai en conditions réelles :

    Le dispatcheur travaille normalement. Pour chaque course, il saisit le
    coursier qu'il vient d'attribuer, PUIS l'application révèle ce qu'elle
    aurait décidé. L'ordre compte : révéler d'abord la réponse de l'app
    biaiserait le choix humain et l'essai ne vaudrait plus rien.

Deux règles structurantes :

1. C'est le choix HUMAIN qui est appliqué à la flotte, jamais celui de l'app.
   Le dispatcheur reste maître de son exploitation ; l'application observe.
   Sans ça, l'état simulé divergerait du terrain dès le premier désaccord et
   toutes les comparaisons suivantes seraient faussées.

2. Chaque comparaison est journalisée avec le classement complet, pour que le
   désaccord soit analysable a posteriori — et pas seulement compté.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from app.models.order import Order
from app.services.dispatch import motif_inegibilite, score_detail
from app.services.fleet import FleetManager
from app.services import storage


@dataclass
class CandidatEvalue:
    """Un coursier vu par le moteur pour une course donnée."""
    code: str
    vehicle_type: str
    eligible: bool
    motif_inegibilite: Optional[str]
    rang: Optional[int]              # 1 = meilleur ; None si inéligible
    score: Optional[float]
    distance_km: Optional[float]
    penalite_charge: Optional[float]
    penalite_vehicule: Optional[float]
    penalite_retard: Optional[float]
    detour_km: Optional[float]
    explications: list[str] = field(default_factory=list)
    charge: int = 0
    capacite: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "vehicle_type": self.vehicle_type,
            "eligible": self.eligible,
            "motif_inegibilite": self.motif_inegibilite,
            "rang": self.rang,
            "score": round(self.score, 3) if self.score is not None else None,
            "distance_km": round(self.distance_km, 2) if self.distance_km is not None else None,
            "penalite_charge": round(self.penalite_charge, 2) if self.penalite_charge is not None else None,
            "penalite_vehicule": round(self.penalite_vehicule, 2) if self.penalite_vehicule is not None else None,
            "penalite_retard": round(self.penalite_retard, 2) if self.penalite_retard is not None else None,
            "detour_km": round(self.detour_km, 2) if self.detour_km is not None else None,
            "explications": self.explications,
            "charge": self.charge,
            "capacite": self.capacite,
        }


def classer_coursiers(order: Order, fleet: FleetManager) -> list[CandidatEvalue]:
    """
    Évalue TOUTE la flotte pour une course — éligibles comme écartés.

    Les éligibles sont triés par score croissant (meilleur en tête) et reçoivent
    un rang. Les écartés viennent ensuite, avec le motif de leur exclusion :
    le dispatcheur doit pouvoir vérifier qu'un coursier absent du classement
    l'est pour une bonne raison, et pas par bug.
    """
    eligibles: list[tuple[float, CandidatEvalue]] = []
    ecartes: list[CandidatEvalue] = []

    for coursier in fleet.list_coursiers():
        motif = motif_inegibilite(coursier, order)
        if motif is not None:
            ecartes.append(CandidatEvalue(
                code=coursier.code,
                vehicle_type=coursier.vehicle_type.value,
                eligible=False,
                motif_inegibilite=motif,
                rang=None, score=None, distance_km=None,
                penalite_charge=None, penalite_vehicule=None, penalite_retard=None, detour_km=None,
                charge=coursier.current_load, capacite=coursier.max_load,
            ))
            continue

        detail = score_detail(coursier, order, fleet)
        eligibles.append((detail.total, CandidatEvalue(
            code=coursier.code,
            vehicle_type=coursier.vehicle_type.value,
            eligible=True,
            motif_inegibilite=None,
            rang=None,  # rempli après le tri
            score=detail.total,
            distance_km=detail.distance_km,
            penalite_charge=detail.penalite_charge,
            penalite_vehicule=detail.penalite_vehicule,
            penalite_retard=detail.penalite_retard,
            detour_km=detail.detour_km,
            explications=detail.explications(),
            charge=coursier.current_load,
            capacite=coursier.max_load,
        )))

    eligibles.sort(key=lambda t: t[0])
    classement = []
    for rang, (_, candidat) in enumerate(eligibles, start=1):
        candidat.rang = rang
        classement.append(candidat)

    # Les écartés ferment la liste, triés par code pour un affichage stable
    classement.extend(sorted(ecartes, key=lambda c: c.code))
    return classement


@dataclass
class ResultatComparaison:
    """Le verdict d'une comparaison, tel qu'affiché au dispatcheur."""
    order_id: str
    horodatage: str
    choix_manuel: Optional[str]
    choix_app: Optional[str]
    accord: bool
    rang_manuel: Optional[int]
    score_manuel: Optional[float]
    score_app: Optional[float]
    ecart_km: Optional[float]
    verdict: str
    classement: list[CandidatEvalue]
    journal_id: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "journal_id": self.journal_id,
            "order_id": self.order_id,
            "horodatage": self.horodatage,
            "choix_manuel": self.choix_manuel,
            "choix_app": self.choix_app,
            "accord": self.accord,
            "rang_manuel": self.rang_manuel,
            "score_manuel": round(self.score_manuel, 3) if self.score_manuel is not None else None,
            "score_app": round(self.score_app, 3) if self.score_app is not None else None,
            "ecart_km": round(self.ecart_km, 2) if self.ecart_km is not None else None,
            "verdict": self.verdict,
            "classement": [c.to_dict() for c in self.classement],
        }


def _formuler_verdict(
    choix_manuel: Optional[str],
    choix_app: Optional[str],
    rang_manuel: Optional[int],
    ecart_km: Optional[float],
    classement: list[CandidatEvalue],
) -> str:
    """Rédige la phrase affichée en gros après la révélation."""
    if choix_app is None:
        return "Aucun coursier éligible : l'application n'aurait pas pu attribuer cette course."

    if choix_manuel is None:
        nb = sum(1 for c in classement if c.eligible)
        return f"L'application aurait choisi {choix_app} parmi {nb} coursier(s) éligible(s)."

    if choix_manuel == choix_app:
        return f"Accord : vous et l'application avez tous les deux retenu {choix_manuel}."

    motif = next((c.motif_inegibilite for c in classement if c.code == choix_manuel and not c.eligible), None)
    if motif:
        return (
            f"Désaccord : l'application aurait pris {choix_app}. "
            f"Elle écarte {choix_manuel} — {motif}."
        )

    if rang_manuel is not None and ecart_km is not None:
        return (
            f"Désaccord : l'application aurait pris {choix_app}. "
            f"{choix_manuel} arrive {rang_manuel}e de son classement, à {ecart_km:.1f} km équivalents."
        )
    return f"Désaccord : l'application aurait pris {choix_app}, vous avez retenu {choix_manuel}."


def comparer(
    order: Order,
    fleet: FleetManager,
    choix_manuel: Optional[str] = None,
    commentaire: Optional[str] = None,
    journaliser: bool = True,
) -> ResultatComparaison:
    """
    Compare la décision humaine à celle du moteur pour une course.

    Args:
        order        : La course à attribuer (déjà géocodée).
        fleet        : État courant de la flotte.
        choix_manuel : Code du coursier réellement choisi par le dispatcheur.
                       None = simple simulation, sans décision humaine à comparer.
        commentaire  : Note libre du dispatcheur (« client exigeant », « il rentrait au dépôt »…).
        journaliser  : False pour une simulation à blanc, qui ne touche ni au
                       journal ni à l'état de la flotte.

    Returns:
        ResultatComparaison — le classement complet et le verdict.
    """
    classement = classer_coursiers(order, fleet)
    eligibles  = [c for c in classement if c.eligible]

    choix_app  = eligibles[0].code if eligibles else None
    score_app  = eligibles[0].score if eligibles else None

    code_manuel = choix_manuel.upper() if choix_manuel else None
    candidat_manuel = next((c for c in classement if c.code == code_manuel), None) if code_manuel else None

    rang_manuel  = candidat_manuel.rang  if candidat_manuel else None
    score_manuel = candidat_manuel.score if candidat_manuel else None
    ecart_km = (
        score_manuel - score_app
        if score_manuel is not None and score_app is not None
        else None
    )

    accord  = bool(code_manuel and choix_app and code_manuel == choix_app)
    verdict = _formuler_verdict(code_manuel, choix_app, rang_manuel, ecart_km, classement)

    resultat = ResultatComparaison(
        order_id=order.id,
        horodatage=datetime.now().isoformat(timespec="seconds"),
        choix_manuel=code_manuel,
        choix_app=choix_app,
        accord=accord,
        rang_manuel=rang_manuel,
        score_manuel=score_manuel,
        score_app=score_app,
        ecart_km=ecart_km,
        verdict=verdict,
        classement=classement,
    )

    if journaliser:
        resultat.journal_id = storage.enregistrer_comparaison({
            "horodatage": resultat.horodatage,
            "order_id": order.id,
            "zone": order.zone.value,
            "volume_type": order.volume_type.value,
            "client_tier": order.client_tier.value,
            "deadline_minutes": order.deadline_minutes,
            "pickup_lat": order.pickup.lat,
            "pickup_lon": order.pickup.lon,
            "delivery_lat": order.delivery.lat,
            "delivery_lon": order.delivery.lon,
            "choix_manuel": code_manuel,
            "choix_app": choix_app,
            "accord": accord,
            "rang_manuel": rang_manuel,
            "score_manuel": score_manuel,
            "score_app": score_app,
            "ecart_km": ecart_km,
            "commentaire": commentaire,
            "classement_json": [c.to_dict() for c in classement],
        })

        # La flotte suit le terrain, donc le choix humain — jamais celui de l'app.
        if code_manuel and fleet.get_coursier(code_manuel):
            fleet.add_order(order)
            fleet.assign_order_to_coursier(order, code_manuel)

    return resultat
