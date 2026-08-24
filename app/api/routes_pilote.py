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
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from fastapi.templating import Jinja2Templates

from app.api.routes import _coursier_to_response
from app.api.schemas import ComparaisonRequest, CourseExistanteRequest, CoursierResponse
from app.models.enums import OrderStatus
from app.models.order import Coordinates, Order
from app.services import storage
from app.services.comparaison import comparer
from app.services.fleet import fleet_manager

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
    """Sert l'interface de pilotage utilisée quotidiennement pendant l'essai."""
    return templates.TemplateResponse(request, "pilote.html")


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
    fleet_manager.assign_order_to_coursier(order, coursier.code)
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
