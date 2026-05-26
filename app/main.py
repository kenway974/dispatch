"""
Point d'entrée de l'application FastAPI.

Démarrage :
    uvicorn app.main:app --reload

Documentation interactive :
    http://localhost:8000/docs   (Swagger UI)
    http://localhost:8000/redoc  (ReDoc)

Interface de démo prospect :
    http://localhost:8000/
"""

from fastapi import FastAPI

from app.api.routes import router as api_router
from app.api.routes_ui import router as ui_router

app = FastAPI(
    title="Dispatch Engine — Coursiers Écologiques Paris",
    description=(
        "Moteur d'attribution automatique en temps réel pour une flotte de coursiers écologiques. "
        "Optimise les assignations selon la zone géographique, le volume des colis, "
        "la charge actuelle des coursiers et les opportunités de groupage."
    ),
    version="1.0.0",
)

app.include_router(ui_router)   # page de démo + uploads (en premier pour capturer GET /)
app.include_router(api_router)  # REST API
