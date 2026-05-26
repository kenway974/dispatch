"""
Script de peuplement de la flotte officielle.

Lance l'API puis exécute ce script pour avoir une flotte prête à tester :
    python scripts/seed_fleet.py

Flotte officielle — 12 coursiers :
  scoot_banlieue_proche (Paris + Petite Couronne, 50cc) : KEN, MEH, LIM, MIC, MAT
  scoot_banlieue_loin   (Petite + Grande Couronne, 125cc+) : JC, MEF, ABD
  longue_distance        (toutes zones) : JEA, SET
  fourgon                (toutes zones, colis Voiture) : LAH, CAR
"""

import httpx

BASE_URL = "http://localhost:8000"

# ---------------------------------------------------------------------------
# Flotte officielle — positions GPS réalistes dans et autour de Paris
# ---------------------------------------------------------------------------
COURIERS = [
    # ── scoot_banlieue_proche — Paris intra-muros + Petite Couronne (50cc) ──
    {
        "code": "KEN",
        "vehicle_type": "scoot_banlieue_proche",
        "lat": 48.8559,   # Paris 4e — Le Marais
        "lon": 2.3578,
    },
    {
        "code": "MEH",
        "vehicle_type": "scoot_banlieue_proche",
        "lat": 48.8780,   # Paris 18e — Montmartre
        "lon": 2.3430,
    },
    {
        "code": "LIM",
        "vehicle_type": "scoot_banlieue_proche",
        "lat": 48.8458,   # Paris 14e — Montparnasse
        "lon": 2.3358,
    },
    {
        "code": "MIC",
        "vehicle_type": "scoot_banlieue_proche",
        "lat": 48.8700,   # Paris 17e — Batignolles
        "lon": 2.3200,
    },
    {
        "code": "MAT",
        "vehicle_type": "scoot_banlieue_proche",
        "lat": 48.8494,   # Paris 12e — Nation
        "lon": 2.3950,
    },

    # ── scoot_banlieue_loin — Petite + Grande Couronne (125cc+, voies rapides OK) ──
    {
        "code": "JC",
        "vehicle_type": "scoot_banlieue_loin",
        "lat": 48.9360,   # Saint-Denis (93)
        "lon": 2.3553,
    },
    {
        "code": "MEF",
        "vehicle_type": "scoot_banlieue_loin",
        "lat": 48.7900,   # Créteil (94)
        "lon": 2.4560,
    },
    {
        "code": "ABD",
        "vehicle_type": "scoot_banlieue_loin",
        "lat": 48.9050,   # Bondy (93)
        "lon": 2.4830,
    },

    # ── longue_distance — spécialisé inter-villes et aéroports ──
    {
        "code": "JEA",
        "vehicle_type": "longue_distance",
        "lat": 48.7262,   # Orly / Paray-Vieille-Poste (94)
        "lon": 2.3596,
    },
    {
        "code": "SET",
        "vehicle_type": "longue_distance",
        "lat": 49.0097,   # Roissy-CDG (95)
        "lon": 2.5479,
    },

    # ── fourgon — colis Voiture, toutes zones ──
    {
        "code": "LAH",
        "vehicle_type": "fourgon",
        "lat": 48.8045,   # Versailles / Le Chesnay (78)
        "lon": 2.1200,
    },
    {
        "code": "CAR",
        "vehicle_type": "fourgon",
        "lat": 48.7773,   # Vitry-sur-Seine (94)
        "lon": 2.4007,
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
                print(f"  ✓ {c['code']:3s} ({c['vehicle_type']:24s}) — ({c['lat']:.4f}, {c['lon']:.4f})")
            elif resp.status_code == 409:
                print(f"  ~ {data['code']} déjà enregistré, ignoré.")
            else:
                print(f"  ✗ Erreur pour {data['code']} : {resp.text}")

        # Résumé final
        resp = client.get("/health")
        h = resp.json()
        print(f"\nFlotte prête : {h['active_couriers']} coursiers actifs sur {h['courier_count']} enregistrés.")


if __name__ == "__main__":
    seed()
