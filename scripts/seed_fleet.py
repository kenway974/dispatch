"""
Script de peuplement de la flotte avec des coursiers de démonstration.

Lance l'API puis exécute ce script pour avoir une flotte prête à tester :
    python scripts/seed_fleet.py

Les coursiers sont positionnés sur des points réels de Paris et sa banlieue.
"""

import httpx

BASE_URL = "http://localhost:8000"

# Données de la flotte de démonstration
# Coordonnées réelles des zones concernées
COURIERS = [
    # ---- Paris intra-muros (scoot_ville) ----
    {
        "code": "KEN",
        "vehicle_type": "scoot_ville",
        "lat": 48.8566,   # Paris centre (Île de la Cité)
        "lon": 2.3522,
    },
    {
        "code": "THO",
        "vehicle_type": "scoot_ville",
        "lat": 48.8864,   # Montmartre
        "lon": 2.3432,
    },
    {
        "code": "ALI",
        "vehicle_type": "scoot_ville",
        "lat": 48.8533,   # Bastille
        "lon": 2.3692,
    },
    # ---- Petite Couronne (scoot_banlieue_proche) ----
    {
        "code": "MAR",
        "vehicle_type": "scoot_banlieue_proche",
        "lat": 48.9360,   # Saint-Denis
        "lon": 2.3553,
    },
    {
        "code": "LEA",
        "vehicle_type": "scoot_banlieue_proche",
        "lat": 48.8948,   # Aubervilliers
        "lon": 2.3833,
    },
    # ---- Grande Couronne (scoot_banlieue_loin) ----
    {
        "code": "SAM",
        "vehicle_type": "scoot_banlieue_loin",
        "lat": 48.9906,   # Sarcelles
        "lon": 2.3797,
    },
    # ---- Fourgon (Grande Couronne + Voiture) ----
    {
        "code": "FOU",
        "vehicle_type": "fourgon",
        "lat": 48.8045,   # Versailles
        "lon": 2.1200,
    },
    {
        "code": "MAX",
        "vehicle_type": "fourgon",
        "lat": 48.7773,   # Créteil
        "lon": 2.4555,
    },
]


def seed() -> None:
    print(f"Connexion à {BASE_URL}...")

    with httpx.Client(base_url=BASE_URL, timeout=10.0) as client:
        # Vérifie que l'API est disponible
        resp = client.get("/health")
        resp.raise_for_status()
        print(f"API en ligne. Flotte actuelle : {resp.json()['courier_count']} coursier(s).\n")

        # Crée chaque coursier
        for data in COURIERS:
            resp = client.post("/couriers", json=data)
            if resp.status_code == 201:
                c = resp.json()
                print(f"  ✓ {c['code']} ({c['vehicle_type']}) — position ({c['lat']}, {c['lon']})")
            elif resp.status_code == 409:
                print(f"  ~ {data['code']} déjà enregistré, ignoré.")
            else:
                print(f"  ✗ Erreur pour {data['code']} : {resp.text}")

        # Résumé final
        resp = client.get("/health")
        h = resp.json()
        print(f"\nFlotte prête : {h['active_couriers']} coursiers actifs.")


if __name__ == "__main__":
    seed()
