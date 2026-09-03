"""
Endpoints du mode pilote — l'essai en conditions réelles.

Routes :
  GET   /pilote                              → l'interface de pilotage
  POST  /pilote/comparaison                  → journalise une décision et révèle celle de l'app
  POST  /pilote/simulation                   → simulation à blanc (rien n'est journalisé ni attribué)
  GET   /pilote/journal                      → journal + statistiques cumulées
  DELETE /pilote/journal/{entry_id}          → supprime une saisie erronée
  POST  /pilote/journal/reset                → vide le journal
  GET   /pilote/journal/export.csv           → export CSV de l'essai
  POST  /coursiers/{code}/courses            → déclarer une course déjà en portefeuille
  DELETE /coursiers/{code}/courses/{order_id} → course livrée : libère la charge
  GET   /suivi/{code}                        → page de suivi ouverte par le coursier (secours)
  POST  /coursiers/{code}/ping               → position remontée par son téléphone
  POST  /positions/import                    → reprise des positions du système de l'entreprise
  GET   /pilote/positions                    → positions exploitables + fraîcheur
  GET   /pilote/echanges                     → courses qui gagneraient à changer de mains
"""

from __future__ import annotations

import csv
import io
import os
import secrets
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from fastapi.templating import Jinja2Templates

from app.config import DEPOT_ADRESSE, NIVEAUX_URGENCE
from app.api.routes import _coursier_to_response
from app.api.schemas import (
    ComparaisonRequest,
    CourseExistanteRequest,
    CoursierResponse,
    ImportPositionsRequest,
    PingPositionRequest,
)
from app.models.enums import OrderStatus, PositionSource
from app.models.order import Coordinates, Order
from app.services import storage
from app.services.comparaison import comparer
from app.services.fleet import fleet_manager
from app.services.position import estimer_position
from app.services.reattribution import proposer_echanges

router = APIRouter(tags=["Mode pilote"])

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def _construire_order(payload: ComparaisonRequest | CourseExistanteRequest, prefixe: str) -> Order:
    """Fabrique un Order à partir d'un corps de requête, en garantissant un ID unique."""
    order_id = (payload.id or "").strip() or f"{prefixe}-{uuid.uuid4().hex[:6].upper()}"
    if fleet_manager.get_order(order_id):
        order_id = f"{order_id}-{uuid.uuid4().hex[:4].upper()}"

    return Order(
        id=order_id,
        pickup=Coordinates(lat=payload.pickup_lat, lon=payload.pickup_lon),
        delivery=Coordinates(lat=payload.delivery_lat, lon=payload.delivery_lon),
        zone=payload.zone,
        volume_type=payload.volume_type,
        client_tier=getattr(payload, "client_tier", None) or Order.model_fields["client_tier"].default,
        deadline_minutes=getattr(payload, "deadline_minutes", None),
    )


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------

@router.get("/pilote", include_in_schema=False)
def page_pilote(request: Request):
    """
    Sert l'interface de pilotage utilisée quotidiennement pendant l'essai.

    Les paliers d'urgence et l'adresse du dépôt viennent de la configuration :
    le dispatcheur saisit dans les mots du métier, pas en tapant des minutes.
    """
    return templates.TemplateResponse(
        request,
        "pilote.html",
        {
            "niveaux_urgence": NIVEAUX_URGENCE,
            "depot_adresse": DEPOT_ADRESSE,
        },
    )


# ---------------------------------------------------------------------------
# Comparaison
# ---------------------------------------------------------------------------

@router.post("/pilote/comparaison", status_code=status.HTTP_201_CREATED)
def enregistrer_comparaison(payload: ComparaisonRequest) -> dict:
    """
    Enregistre la décision du dispatcheur et révèle celle de l'application.

    Effets :
    - la comparaison entre au journal (elle compte dans le taux d'accord) ;
    - la course est attribuée au coursier CHOISI PAR LE DISPATCHEUR, pour que
      l'état de la flotte continue de refléter le terrain.
    """
    if payload.choix_manuel:
        code = payload.choix_manuel.upper()
        if fleet_manager.get_coursier(code) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Coursier '{code}' introuvable dans la flotte.",
            )

    order = _construire_order(payload, "PIL")
    resultat = comparer(
        order,
        fleet_manager,
        choix_manuel=payload.choix_manuel,
        commentaire=payload.commentaire,
        journaliser=True,
    )
    reponse = resultat.to_dict()
    reponse["statistiques"] = storage.statistiques()
    return reponse


@router.post("/pilote/simulation")
def simuler(payload: ComparaisonRequest) -> dict:
    """
    Simulation à blanc : montre le classement sans rien journaliser ni attribuer.

    Utile pour explorer une hypothèse (« et si le colis était Premium ? ») sans
    polluer les statistiques de l'essai.
    """
    order = _construire_order(payload, "SIM")
    return comparer(
        order,
        fleet_manager,
        choix_manuel=payload.choix_manuel,
        journaliser=False,
    ).to_dict()


# ---------------------------------------------------------------------------
# Journal
# ---------------------------------------------------------------------------

@router.get("/pilote/journal")
def lire_journal(limite: int = 200) -> dict:
    """Retourne le journal des comparaisons et les statistiques cumulées de l'essai."""
    return {
        "statistiques": storage.statistiques(),
        "entrees": storage.lister_comparaisons(limite=limite),
    }


@router.delete("/pilote/journal/{entry_id}")
def supprimer_entree(entry_id: int) -> dict:
    """Supprime une saisie erronée du journal (sans toucher à l'état de la flotte)."""
    if not storage.supprimer_comparaison(entry_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entrée introuvable.")
    return {"status": "ok", "supprimee": entry_id, "statistiques": storage.statistiques()}


@router.post("/pilote/journal/reset")
def vider_journal() -> dict:
    """Vide le journal — à n'utiliser qu'entre deux campagnes d'essai."""
    supprimees = storage.vider_journal()
    return {"status": "ok", "supprimees": supprimees}


@router.get("/pilote/journal/export.csv", include_in_schema=False)
def exporter_journal():
    """Exporte le journal en CSV, ouvrable tel quel dans Excel."""
    entrees = storage.lister_comparaisons()
    colonnes = [
        "id", "horodatage", "order_id", "zone", "volume_type", "client_tier",
        "deadline_minutes", "choix_manuel", "choix_app", "accord", "rang_manuel",
        "score_manuel", "score_app", "ecart_km", "commentaire",
    ]

    tampon = io.StringIO()
    tampon.write("﻿")  # BOM : Excel ouvre l'UTF-8 correctement
    writer = csv.DictWriter(tampon, fieldnames=colonnes, delimiter=";", extrasaction="ignore")
    writer.writeheader()
    for entree in entrees:
        ligne = {c: entree.get(c) for c in colonnes}
        ligne["accord"] = "oui" if entree.get("accord") else "non"
        # Arrondi à l'export : trois décimales de flottant brut ne servent personne dans Excel
        for champ in ("score_manuel", "score_app", "ecart_km"):
            if ligne.get(champ) is not None:
                ligne[champ] = round(float(ligne[champ]), 2)
        writer.writerow(ligne)
    tampon.seek(0)

    horodatage = datetime.now().strftime("%Y-%m-%d")
    return StreamingResponse(
        tampon,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=pilote_dispatch_{horodatage}.csv"},
    )


# ---------------------------------------------------------------------------
# Courses déjà en portefeuille
# ---------------------------------------------------------------------------

@router.post("/coursiers/{code}/courses", status_code=status.HTTP_201_CREATED, tags=["Coursiers"])
def declarer_course(code: str, payload: CourseExistanteRequest) -> CoursierResponse:
    """
    Déclare une course déjà en cours chez un coursier (aucun dispatch déclenché).

    C'est ainsi que le dispatcheur photographie l'état réel de sa flotte en début
    de service : sans ces courses, le moteur croit tout le monde disponible.
    """
    coursier = fleet_manager.get_coursier(code)
    if coursier is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Coursier '{code.upper()}' introuvable.")

    order = _construire_order(payload, "EXIST")
    fleet_manager.add_order(order)
    fleet_manager.assign_order_to_coursier(
        order, coursier.code, ramassage_effectue=payload.ramassage_effectue
    )
    return _coursier_to_response(fleet_manager.get_coursier(coursier.code))


@router.delete("/coursiers/{code}/courses/{order_id}", tags=["Coursiers"])
def cloturer_course(code: str, order_id: str) -> CoursierResponse:
    """
    Retire une course du portefeuille d'un coursier (livrée ou annulée).

    Indispensable sur une journée complète : sans clôture, tout le monde finit
    saturé et le moteur ne trouve plus personne d'éligible.
    """
    coursier = fleet_manager.get_coursier(code)
    if coursier is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Coursier '{code.upper()}' introuvable.")
    if not any(o.order_id == order_id for o in coursier.assigned_orders):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Course '{order_id}' absente du portefeuille de {coursier.code}.",
        )

    fleet_manager.remove_order_from_coursier(order_id, coursier.code)
    order = fleet_manager.get_order(order_id)
    if order is not None:
        order.status = OrderStatus.DELIVERED
    return _coursier_to_response(fleet_manager.get_coursier(coursier.code))


# ---------------------------------------------------------------------------
# Suivi de position
# ---------------------------------------------------------------------------

@router.get("/suivi/{code}", include_in_schema=False)
def page_suivi(code: str, request: Request):
    """
    Page que le coursier ouvre sur son téléphone au début du service.

    Elle envoie sa position toutes les 30 secondes tant qu'elle reste ouverte.
    Rien à installer : c'est une page web, et le code coursier suffit à
    l'identifier — le même code que celui qu'il utilise déjà au quotidien.
    """
    coursier = fleet_manager.get_coursier(code)
    return templates.TemplateResponse(
        request,
        "suivi.html",
        {
            "code": code.upper(),
            "connu": coursier is not None,
            "vehicule": coursier.vehicle_type.value if coursier else None,
        },
    )


@router.post("/coursiers/{code}/ping", tags=["Coursiers"])
def ping_position(code: str, payload: PingPositionRequest) -> dict:
    """
    Enregistre la position remontée par le téléphone d'un coursier.

    Appelé automatiquement par `/suivi/{code}`. Réponse volontairement légère :
    elle part sur le réseau mobile du coursier, plusieurs fois par minute et par
    coursier, toute la journée.
    """
    coursier = fleet_manager.get_coursier(code)
    if coursier is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Coursier '{code.upper()}' introuvable. Vérifiez votre code.",
        )

    fleet_manager.update_coursier_position(
        coursier.code, payload.lat, payload.lon, source=PositionSource.GPS,
    )
    return {
        "status": "ok",
        "code": coursier.code,
        "charge": f"{coursier.current_load}/{coursier.max_load}",
        "courses": coursier.order_count,
    }


@router.get("/pilote/positions", tags=["Mode pilote"])
def positions_flotte() -> dict:
    """
    Position exploitable de chaque coursier, avec sa fraîcheur.

    Alimente le rafraîchissement de la carte du dispatcheur sans recharger toute
    la flotte : c'est l'appel le plus fréquent de l'interface.
    """
    coursiers = []
    for coursier in fleet_manager.list_coursiers():
        estimation = estimer_position(coursier)
        coursiers.append({
            "code": coursier.code,
            "vehicle_type": coursier.vehicle_type.value,
            "is_active": coursier.is_active,
            "charge": coursier.current_load,
            "capacite": coursier.max_load,
            "courses": coursier.order_count,
            **estimation.to_dict(),
        })
    return {"coursiers": coursiers}


# ---------------------------------------------------------------------------
# Reprise des positions du système de suivi de l'entreprise
# ---------------------------------------------------------------------------

def _verifier_jeton_import(fourni: Optional[str]) -> None:
    """
    Contrôle le jeton partagé protégeant l'import de positions.

    Cet endpoint déplace des coursiers sur la carte du dispatcheur : laissé
    ouvert, n'importe qui pourrait fausser les recommandations du moteur. Tant
    que DISPATCH_IMPORT_TOKEN n'est pas configuré, l'import est donc refusé —
    fermé par défaut plutôt qu'ouvert par oubli.
    """
    attendu = os.getenv("DISPATCH_IMPORT_TOKEN", "").strip()
    if not attendu:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Import de positions désactivé : définissez la variable "
                "d'environnement DISPATCH_IMPORT_TOKEN pour l'activer."
            ),
        )
    if not fourni or not secrets.compare_digest(fourni, attendu):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Jeton d'import invalide.",
        )


@router.post("/positions/import", tags=["Mode pilote"])
def importer_positions(
    payload: ImportPositionsRequest,
    x_import_token: Optional[str] = Header(default=None, alias="X-Import-Token"),
) -> dict:
    """
    Reprend un lot de positions issues du système de suivi déjà utilisé par l'entreprise.

    C'est la prise unique par laquelle les positions entrent, quelle que soit
    leur origine — appel direct du système source, script de synchronisation
    périodique, ou export repris à la main. Le reste de l'application ignore
    d'où elles viennent.

    Un code inconnu n'est pas une erreur : le système source suit probablement
    plus de coursiers que la flotte de l'essai. Il est signalé, le lot passe.
    """
    _verifier_jeton_import(x_import_token)

    mises_a_jour: list[str] = []
    inconnus: list[str] = []

    for position in payload.positions:
        code = position.code.upper()
        if fleet_manager.get_coursier(code) is None:
            inconnus.append(code)
            continue
        fleet_manager.update_coursier_position(
            code, position.lat, position.lon,
            source=PositionSource.IMPORT,
            horodatage=position.horodatage,
        )
        mises_a_jour.append(code)

    return {
        "status": "ok",
        "mises_a_jour": len(mises_a_jour),
        "codes_mis_a_jour": mises_a_jour,
        "codes_inconnus": inconnus,
    }


@router.get("/pilote/echanges", tags=["Mode pilote"])
def echanges_proposes() -> dict:
    """
    Courses en circulation qui gagneraient à changer de mains, maintenant.

    Le dispatcheur le fait déjà de tête : « tu ramasses dans le 17e, tu viens
    prendre ta pause, tu passes le colis à ton collègue qui ira dans le 2e ».
    Cet endpoint cherche les mêmes occasions et dit ce qu'elles rapportent.

    Une proposition n'apparaît que si les deux coursiers sont assez proches pour
    se passer le colis de la main à la main — c'est ainsi que ça se passe, le
    colis n'attend nulle part.
    """
    propositions = proposer_echanges(fleet_manager)
    return {
        "total": len(propositions),
        "echanges": [e.to_dict() for e in propositions],
    }
