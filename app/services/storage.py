"""
Persistance SQLite du mode pilote.

Deux besoins distincts, deux tables :

- `comparaisons`   : le journal des décisions (la donnée précieuse du pilote —
                     un mois de comparaisons manuel / automatique). Colonnes
                     typées pour rester exploitable en SQL et en CSV.
- `etat_flotte`    : un instantané JSON de la flotte, réécrit à chaque mutation.
                     12 coursiers = quelques kilo-octets, une seule ligne suffit.
                     Sans ça, un redéploiement remet la flotte à zéro en plein
                     service et le dispatcheur doit tout ressaisir.

Uniquement de la bibliothèque standard : aucune dépendance ajoutée.
Chemin du fichier configurable via la variable d'environnement DISPATCH_DB_PATH
(sur Railway, la pointer vers un volume monté pour survivre aux redéploiements).
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

DB_PATH = Path(os.getenv("DISPATCH_DB_PATH", "data/pilote.db"))

# SQLite tolère mal les accès concurrents en écriture depuis plusieurs threads ;
# uvicorn exécute les routes synchrones dans un threadpool, d'où ce verrou.
_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS comparaisons (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    horodatage        TEXT    NOT NULL,
    order_id          TEXT    NOT NULL,
    zone              TEXT    NOT NULL,
    volume_type       TEXT    NOT NULL,
    client_tier       TEXT    NOT NULL,
    deadline_minutes  INTEGER,
    pickup_lat        REAL    NOT NULL,
    pickup_lon        REAL    NOT NULL,
    delivery_lat      REAL    NOT NULL,
    delivery_lon      REAL    NOT NULL,
    choix_manuel      TEXT,
    choix_app         TEXT,
    accord            INTEGER NOT NULL,
    rang_manuel       INTEGER,
    score_manuel      REAL,
    score_app         REAL,
    ecart_km          REAL,
    commentaire       TEXT,
    classement_json   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS etat_flotte (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    payload    TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _connect() -> sqlite3.Connection:
    """Ouvre une connexion, en créant le dossier parent au besoin."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Crée les tables si elles n'existent pas. Idempotent."""
    with _lock, _connect() as conn:
        conn.executescript(SCHEMA)


# ---------------------------------------------------------------------------
# Journal des comparaisons
# ---------------------------------------------------------------------------

def enregistrer_comparaison(entree: dict[str, Any]) -> int:
    """
    Ajoute une comparaison au journal.

    Args:
        entree: dict dont les clés correspondent aux colonnes de `comparaisons`
                (hors `id`). `classement_json` est sérialisé ici si besoin.

    Returns:
        L'identifiant de la ligne créée.
    """
    champs = [
        "horodatage", "order_id", "zone", "volume_type", "client_tier",
        "deadline_minutes", "pickup_lat", "pickup_lon", "delivery_lat", "delivery_lon",
        "choix_manuel", "choix_app", "accord", "rang_manuel",
        "score_manuel", "score_app", "ecart_km", "commentaire", "classement_json",
    ]
    valeurs = []
    for champ in champs:
        v = entree.get(champ)
        if champ == "classement_json" and not isinstance(v, str):
            v = json.dumps(v or [], ensure_ascii=False)
        if isinstance(v, bool):
            v = int(v)
        valeurs.append(v)

    placeholders = ", ".join("?" * len(champs))
    with _lock, _connect() as conn:
        cur = conn.execute(
            f"INSERT INTO comparaisons ({', '.join(champs)}) VALUES ({placeholders})",
            valeurs,
        )
        return int(cur.lastrowid or 0)


def lister_comparaisons(limite: Optional[int] = None) -> list[dict[str, Any]]:
    """Retourne le journal, de la plus récente à la plus ancienne."""
    requete = "SELECT * FROM comparaisons ORDER BY id DESC"
    params: tuple = ()
    if limite is not None:
        requete += " LIMIT ?"
        params = (limite,)
    with _lock, _connect() as conn:
        lignes = conn.execute(requete, params).fetchall()

    resultat = []
    for ligne in lignes:
        d = dict(ligne)
        d["accord"] = bool(d["accord"])
        d["classement"] = json.loads(d.pop("classement_json") or "[]")
        resultat.append(d)
    return resultat


def statistiques() -> dict[str, Any]:
    """
    Agrégats du pilote : le chiffre que le patron regarde en premier.

    - total / accords / desaccords / taux_accord
    - taux_top3 : part des cas où SON choix figurait dans le top 3 de l'app
                  (un désaccord sur le 2e n'a pas le même poids qu'un écart total)
    - ecart_moyen_km : coût moyen en km d'un désaccord
    - par_coursier : volume attribué par chacun, vu du manuel et vu de l'app
    """
    with _lock, _connect() as conn:
        lignes = conn.execute(
            "SELECT accord, rang_manuel, ecart_km, choix_manuel, choix_app FROM comparaisons"
        ).fetchall()

    total = len(lignes)
    if total == 0:
        return {
            "total": 0, "accords": 0, "desaccords": 0, "taux_accord": None,
            "taux_top3": None, "ecart_moyen_km": None, "par_coursier": [],
        }

    accords = sum(1 for l in lignes if l["accord"])
    rangs = [l["rang_manuel"] for l in lignes if l["rang_manuel"] is not None]
    dans_top3 = sum(1 for r in rangs if r <= 3)
    ecarts = [l["ecart_km"] for l in lignes if l["ecart_km"] is not None]

    compteurs: dict[str, dict[str, int]] = {}
    for l in lignes:
        for code, cle in ((l["choix_manuel"], "manuel"), (l["choix_app"], "app")):
            if code:
                compteurs.setdefault(code, {"manuel": 0, "app": 0})[cle] += 1

    par_coursier = [
        {"code": code, "manuel": c["manuel"], "app": c["app"], "ecart": c["app"] - c["manuel"]}
        for code, c in sorted(compteurs.items(), key=lambda kv: -max(kv[1]["manuel"], kv[1]["app"]))
    ]

    return {
        "total": total,
        "accords": accords,
        "desaccords": total - accords,
        "taux_accord": round(100.0 * accords / total, 1),
        "taux_top3": round(100.0 * dans_top3 / len(rangs), 1) if rangs else None,
        "ecart_moyen_km": round(sum(ecarts) / len(ecarts), 2) if ecarts else None,
        "par_coursier": par_coursier,
    }


def supprimer_comparaison(entry_id: int) -> bool:
    """Supprime une entrée du journal (saisie erronée). True si une ligne a sauté."""
    with _lock, _connect() as conn:
        cur = conn.execute("DELETE FROM comparaisons WHERE id = ?", (entry_id,))
        return cur.rowcount > 0


def vider_journal() -> int:
    """Vide entièrement le journal. Retourne le nombre de lignes supprimées."""
    with _lock, _connect() as conn:
        cur = conn.execute("DELETE FROM comparaisons")
        return cur.rowcount


# ---------------------------------------------------------------------------
# Instantané de la flotte
# ---------------------------------------------------------------------------

def sauver_etat_flotte(payload: dict[str, Any]) -> None:
    """Écrase l'instantané de la flotte (une seule ligne, id = 1)."""
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO etat_flotte (id, payload, updated_at) VALUES (1, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET payload = excluded.payload, updated_at = excluded.updated_at",
            (json.dumps(payload, ensure_ascii=False, default=str), datetime.now().isoformat(timespec="seconds")),
        )


def charger_etat_flotte() -> Optional[dict[str, Any]]:
    """Retourne le dernier instantané, ou None si aucun n'a été sauvegardé."""
    with _lock, _connect() as conn:
        ligne = conn.execute("SELECT payload FROM etat_flotte WHERE id = 1").fetchone()
    return json.loads(ligne["payload"]) if ligne else None
