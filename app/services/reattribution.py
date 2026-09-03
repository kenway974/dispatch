"""
Ré-attribution d'une course déjà confiée à un coursier.

Ce que fait le dispatcheur, et que le moteur doit savoir proposer : reprendre
une course en cours et la basculer sur un collègue mieux placé. Le colis passe
de main à main — les deux coursiers sont au même endroit au moment de la
passation, et rien n'attend nulle part.

Le point de passation n'est pas un lieu fixe. Souvent le bureau, parce qu'on y
prend sa pause ; ailleurs si c'est là qu'on se croise.

Ce n'est pas un découpage de course : le ramassage et la livraison restent
solidaires, c'est le titulaire qui change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.config import DISTANCE_PASSATION_MAX_KM, GAIN_MINIMUM_ECHANGE_KM
from app.models.coursier import Coursier, GpsPosition
from app.services.dispatch import motif_inegibilite, score_detail
from app.services.fleet import FleetManager
from app.services.geo import haversine
from app.services.position import position_effective


@dataclass
class Echange:
    """Une passation que le moteur propose au dispatcheur."""
    order_id: str
    porteur: str            # celui qui l'a aujourd'hui
    repreneur: str          # celui à qui elle irait mieux
    gain_km: float          # ce que l'entreprise y gagne, en km équivalents
    score_porteur: float
    score_repreneur: float
    point_passation: GpsPosition
    motif: str

    def to_dict(self) -> dict:
        return {
            "order_id": self.order_id,
            "porteur": self.porteur,
            "repreneur": self.repreneur,
            "gain_km": round(self.gain_km, 2),
            "score_porteur": round(self.score_porteur, 2),
            "score_repreneur": round(self.score_repreneur, 2),
            "lat": round(self.point_passation.lat, 6),
            "lon": round(self.point_passation.lon, 6),
            "motif": self.motif,
        }


def _sans_cette_course(coursier: Coursier, order_id: str) -> Coursier:
    """
    Copie du coursier délestée de la course évaluée.

    Indispensable pour comparer honnêtement : noter le porteur avec la course
    encore dans son portefeuille reviendrait à la compter deux fois, et son
    score serait artificiellement bon — la course serait toujours « déjà sur sa
    route », puisqu'elle y est.
    """
    allege = coursier.model_copy(deep=True)
    allege.assigned_orders = [o for o in allege.assigned_orders if o.order_id != order_id]
    return allege


def _formuler_motif(detail_porteur, detail_repreneur) -> str:
    """Dit en clair ce qui justifie la passation."""
    if detail_porteur.penalite_debordement > 1:
        cause = detail_porteur.motif_debordement or "il s'arrête bientôt"
        return f"{cause} — le repreneur est disponible"
    if detail_porteur.penalite_cumul_urgences > 1:
        return "il court déjà après une urgence — le repreneur est libre"
    if detail_porteur.penalite_retard > 1:
        return "elle mettrait en retard une de ses livraisons promises"
    ecart = detail_porteur.detour_km - detail_repreneur.detour_km
    return f"le repreneur fait {ecart:.1f} km de détour en moins"


def proposer_echanges(
    fleet: FleetManager,
    gain_minimum_km: float = GAIN_MINIMUM_ECHANGE_KM,
    distance_passation_max_km: float = DISTANCE_PASSATION_MAX_KM,
) -> list[Echange]:
    """
    Cherche les courses qui gagneraient à changer de mains, ici et maintenant.

    Pour chaque course en circulation, on se demande si un collègue à portée de
    bras ferait nettement mieux. Trois conditions, toutes nécessaires :

    1. les deux coursiers peuvent se passer le colis — ils sont au même endroit ;
    2. le repreneur est éligible pour cette course ;
    3. le gain dépasse le seuil, sinon la manipulation coûte plus qu'elle ne rapporte.

    Returns:
        Les propositions, de la plus profitable à la moins profitable.
    """
    propositions: list[Echange] = []

    for porteur in fleet.list_coursiers():
        position_porteur = position_effective(porteur)

        for embarquee in porteur.assigned_orders:
            order = fleet.get_order(embarquee.order_id)
            if order is None:
                continue

            porteur_allege = _sans_cette_course(porteur, order.id)
            detail_porteur = score_detail(porteur_allege, order, fleet)

            meilleur: Optional[tuple[float, Coursier, object]] = None
            for candidat in fleet.list_coursiers():
                if candidat.code == porteur.code:
                    continue
                if haversine(position_porteur, position_effective(candidat)) > distance_passation_max_km:
                    continue
                if motif_inegibilite(candidat, order) is not None:
                    continue

                detail_candidat = score_detail(candidat, order, fleet)
                if meilleur is None or detail_candidat.total < meilleur[0]:
                    meilleur = (detail_candidat.total, candidat, detail_candidat)

            if meilleur is None:
                continue

            score_repreneur, repreneur, detail_repreneur = meilleur
            gain = detail_porteur.total - score_repreneur
            if gain < gain_minimum_km:
                continue

            propositions.append(Echange(
                order_id=order.id,
                porteur=porteur.code,
                repreneur=repreneur.code,
                gain_km=gain,
                score_porteur=detail_porteur.total,
                score_repreneur=score_repreneur,
                point_passation=position_porteur,
                motif=_formuler_motif(detail_porteur, detail_repreneur),
            ))

    propositions.sort(key=lambda e: -e.gain_km)
    return propositions


@dataclass
class Transfert:
    """Une course déplacée d'office, parce que son porteur ne peut plus rouler."""
    order_id: str
    porteur: str
    repreneur: str
    score_repreneur: float


def delester_coursier(fleet: FleetManager, code: str) -> list[Transfert]:
    """
    Vide le portefeuille d'un coursier immobilisé et le met hors service.

    Crevaison, panne, malaise : il appelle le dispatch, et on répartit ses
    courses sur les collègues proches ou peu chargés.

    **Jamais déclenché automatiquement.** Accumuler du retard n'est pas un motif :
    chacun son rythme, et un coursier qu'on délesterait pour cause de lenteur
    aurait raison de le prendre mal. Cette fonction ne s'appelle qu'à la demande
    du dispatcheur, prévenu par le coursier lui-même. Deux différences avec une
    passation ordinaire :

    - **le collègue se déplace**, donc on n'exige pas qu'ils soient déjà côte à
      côte ; c'est tout l'inverse d'un colis qu'on se passe en se croisant ;
    - **la flotte est réellement modifiée**, pas seulement conseillée. Le
      coursier est en panne : il n'y a rien à proposer, il faut agir.

    Les courses sont traitées une par une, la flotte étant remise à jour entre
    chaque : sans cela on empilerait tout le portefeuille sur le même collègue
    sans jamais voir sa charge monter.

    Returns:
        Les transferts effectués. Une course sans repreneur éligible n'y figure
        pas — elle reste attribuée au coursier en panne, à traiter à la main.
    """
    en_panne = fleet.get_coursier(code)
    if en_panne is None:
        return []

    transferts: list[Transfert] = []

    for embarquee in list(en_panne.assigned_orders):
        order = fleet.get_order(embarquee.order_id)
        if order is None:
            continue

        candidats = [
            c for c in fleet.list_coursiers()
            if c.code != en_panne.code and motif_inegibilite(c, order) is None
        ]
        if not candidats:
            continue

        repreneur = min(candidats, key=lambda c: score_detail(c, order, fleet).total)
        score = score_detail(repreneur, order, fleet).total

        fleet.remove_order_from_coursier(order.id, en_panne.code)
        fleet.assign_order_to_coursier(
            order, repreneur.code, ramassage_effectue=embarquee.ramassage_effectue
        )
        transferts.append(Transfert(order.id, en_panne.code, repreneur.code, score))

    fleet.set_coursier_active(en_panne.code, False)
    return transferts
