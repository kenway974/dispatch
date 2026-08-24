#!/usr/bin/env python3
"""
Synchronise les positions des coursiers depuis le système de suivi de l'entreprise.

Contexte
--------
L'entreprise dispose déjà d'une application sur laquelle chaque coursier ouvre
son shift ; le dispatcheur y voit les positions en direct. Le moteur n'a pas
besoin de refaire ce travail — il a besoin d'y accéder.

Ce script est le pont : il interroge le système source, traduit sa réponse, et
pousse le résultat sur `POST /positions/import`. Il tourne en boucle, ou une
fois par appel (cron, planificateur Railway, tâche systemd).

Ce qu'il reste à écrire
-----------------------
Une seule fonction : `recuperer_positions_source()`. Tout le reste — traduction,
horodatage, envoi, reprise sur erreur — est déjà en place. Trois cas de figure :

1. **Le système expose une API.** Renseigner l'URL et le jeton, puis mapper les
   champs de sa réponse (voir l'exemple commenté dans la fonction).
2. **Il permet un export CSV.** Lire le fichier déposé et le convertir.
3. **Il n'offre ni l'un ni l'autre.** Alors ce script ne sert pas : l'essai
   fonctionne sur l'estimation de position, sans intégration (cf. README §8).

Usage
-----
    export DISPATCH_URL=https://<projet>.up.railway.app
    export DISPATCH_IMPORT_TOKEN=<le même jeton que côté serveur>
    export SOURCE_URL=...        # système de suivi de l'entreprise
    export SOURCE_TOKEN=...

    python scripts/sync_positions.py --once        # une passe, pour tester
    python scripts/sync_positions.py               # boucle toutes les 60 s
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime
from typing import Any

import httpx

DISPATCH_URL   = os.getenv("DISPATCH_URL", "http://localhost:8000").rstrip("/")
IMPORT_TOKEN   = os.getenv("DISPATCH_IMPORT_TOKEN", "")
SOURCE_URL     = os.getenv("SOURCE_URL", "")
SOURCE_TOKEN   = os.getenv("SOURCE_TOKEN", "")
INTERVALLE_S   = int(os.getenv("SYNC_INTERVALLE_S", "60"))

# Le code utilisé par le système source ne coïncide pas forcément avec le code
# coursier de l'essai. Exemple : {"driver_8821": "KEN"}. Laisser vide si les
# codes sont identiques de part et d'autre.
CORRESPONDANCE_CODES: dict[str, str] = {}


def recuperer_positions_source() -> list[dict[str, Any]]:
    """
    Interroge le système de suivi de l'entreprise.

    Returns:
        Une liste de dicts : {"code": str, "lat": float, "lon": float,
                              "horodatage": datetime | None}

        `horodatage` est l'instant de la MESURE côté source, pas celui de
        l'appel. Le renseigner dès que le système le fournit : sans lui, une
        position relevée il y a dix minutes s'affiche comme temps réel et le
        dispatcheur suit une recommandation périmée sans le savoir.

    Raises:
        NotImplementedError: tant que l'adaptateur n'est pas écrit.
    """
    # ── À REMPLACER une fois l'API du système source connue ──────────────────
    #
    # Forme typique d'une API de suivi de flotte :
    #
    #     reponse = httpx.get(
    #         f"{SOURCE_URL}/api/drivers/locations",
    #         headers={"Authorization": f"Bearer {SOURCE_TOKEN}"},
    #         timeout=15.0,
    #     )
    #     reponse.raise_for_status()
    #
    #     positions = []
    #     for chauffeur in reponse.json()["data"]:
    #         if not chauffeur.get("on_shift"):
    #             continue          # hors service : sa position n'a pas de sens
    #         code_source = str(chauffeur["driver_code"])
    #         positions.append({
    #             "code": CORRESPONDANCE_CODES.get(code_source, code_source).upper(),
    #             "lat": float(chauffeur["location"]["lat"]),
    #             "lon": float(chauffeur["location"]["lng"]),
    #             "horodatage": datetime.fromisoformat(chauffeur["location"]["updated_at"]),
    #         })
    #     return positions
    #
    # ─────────────────────────────────────────────────────────────────────────
    raise NotImplementedError(
        "Adaptateur non écrit : renseignez recuperer_positions_source() une fois "
        "connue l'API du système de suivi de l'entreprise (voir la docstring)."
    )


def pousser(positions: list[dict[str, Any]]) -> dict[str, Any]:
    """Envoie le lot sur l'endpoint d'import du moteur de dispatch."""
    charge = [
        {
            "code": p["code"],
            "lat": p["lat"],
            "lon": p["lon"],
            "horodatage": p["horodatage"].isoformat() if p.get("horodatage") else None,
        }
        for p in positions
    ]
    reponse = httpx.post(
        f"{DISPATCH_URL}/positions/import",
        json={"positions": charge},
        headers={"X-Import-Token": IMPORT_TOKEN},
        timeout=20.0,
    )
    reponse.raise_for_status()
    return reponse.json()


def une_passe() -> bool:
    """Une synchronisation. Retourne False si elle a échoué (sans lever)."""
    try:
        positions = recuperer_positions_source()
    except NotImplementedError as e:
        print(f"⚠️  {e}", file=sys.stderr)
        return False
    except Exception as e:  # noqa: BLE001 — le système source est hors de notre contrôle
        print(f"⚠️  Lecture du système source impossible : {e}", file=sys.stderr)
        return False

    if not positions:
        print("Aucun coursier en service.")
        return True

    try:
        resultat = pousser(positions)
    except Exception as e:  # noqa: BLE001
        print(f"⚠️  Envoi au moteur impossible : {e}", file=sys.stderr)
        return False

    horodatage = datetime.now().strftime("%H:%M:%S")
    message = f"[{horodatage}] {resultat['mises_a_jour']} position(s) à jour"
    if resultat["codes_inconnus"]:
        # Normal : le système source suit toute la flotte, l'essai n'en couvre qu'une partie.
        message += f" · hors essai : {', '.join(sorted(set(resultat['codes_inconnus'])))}"
    print(message)
    return True


def main() -> int:
    parseur = argparse.ArgumentParser(description=__doc__)
    parseur.add_argument("--once", action="store_true", help="une seule passe puis sortie")
    args = parseur.parse_args()

    if not IMPORT_TOKEN:
        print("⚠️  DISPATCH_IMPORT_TOKEN non défini — l'import sera refusé.", file=sys.stderr)
        return 1

    if args.once:
        return 0 if une_passe() else 1

    print(f"Synchronisation toutes les {INTERVALLE_S} s vers {DISPATCH_URL}. Ctrl+C pour arrêter.")
    while True:
        une_passe()
        time.sleep(INTERVALLE_S)


if __name__ == "__main__":
    sys.exit(main())
