"""
Point d'entrée de l'application FastAPI.

Démarrage :
    uvicorn app.main:app --reload

Documentation interactive :
    http://localhost:8000/docs   (Swagger UI)
    http://localhost:8000/redoc  (ReDoc)

Interface de démo prospect :
    http://localhost:8000/

Interface de pilotage (essai en conditions réelles) :
    http://localhost:8000/pilote
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router as api_router
from app.api.routes_pilote import router as pilote_router
from app.api.routes_ui import router as ui_router
from app.services import storage
from app.services.fleet import fleet_manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Restaure l'état persisté au démarrage et branche la sauvegarde continue.

    Sans ça, un redéploiement en plein service viderait la flotte et le
    dispatcheur devrait ressaisir douze coursiers et leurs courses en cours.
    """
    storage.init_db()

    instantane = storage.charger_etat_flotte()
    if instantane:
        fleet_manager.restore(instantane)

    fleet_manager.set_on_change(lambda fleet: storage.sauver_etat_flotte(fleet.to_snapshot()))
    yield
    fleet_manager.set_on_change(None)


app = FastAPI(
    title="Dispatch Engine — Coursiers Écologiques Paris",
    description=(
        "Moteur d'attribution automatique en temps réel pour une flotte de coursiers écologiques. "
        "Optimise les assignations selon la zone géographique, le volume des colis, "
        "la charge actuelle des coursiers et les opportunités de groupage."
    ),
    version="1.1.0",
    lifespan=lifespan,
)

app.include_router(ui_router)      # page de démo + uploads (en premier pour capturer GET /)
app.include_router(pilote_router)  # mode pilote : comparaison manuel / automatique
app.include_router(api_router)     # REST API
