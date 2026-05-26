# Dispatch Engine — Coursiers Écologiques Paris

Moteur de dispatch automatique en temps réel pour une flotte de coursiers écologiques à Paris.  
Chaque commande entrante est analysée et attribuée instantanément au meilleur coursier disponible selon des règles métier strictes : zone géographique, type de véhicule, volume du colis, charge actuelle et optimisation de trajet (groupage).

---

## Table des matières

1. [Vue d'ensemble](#1-vue-densemble)
2. [Architecture du projet](#2-architecture-du-projet)
3. [Règles métier](#3-règles-métier)
4. [Algorithme de dispatch](#4-algorithme-de-dispatch)
5. [Installation](#5-installation)
6. [Démarrage](#6-démarrage)
7. [API REST — Référence complète](#7-api-rest--référence-complète)
8. [Tests](#8-tests)
9. [Configuration](#9-configuration)
10. [Peupler la flotte de démo](#10-peupler-la-flotte-de-démo)
11. [Étendre le projet](#11-étendre-le-projet)

---

## 1. Vue d'ensemble

### Problème résolu

Une boîte de coursiers écologiques parisienne reçoit des commandes en continu via une application cliente. Sans automatisation, le dispatch est manuel, lent, et génère des trajets croisés inutiles (deux scooters qui se doublent pour aller au même quartier).

Ce moteur intercepte chaque commande à sa création et répond en quelques millisecondes avec le coursier optimal.

### Ce que fait le moteur

```
Commande reçue
      │
      ▼
┌─────────────────┐
│ 1. FILTRAGE     │  Élimine les coursiers inéligibles
│                 │  (mauvaise zone, mauvais véhicule, plein)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 2. SCORING      │  Calcule un score pour chaque éligible
│                 │  (distance + charge - bonus groupage)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 3. ATTRIBUTION  │  Attribue au score le plus bas
│                 │  Met à jour coursier + commande
└─────────────────┘
```

---

## 2. Architecture du projet

```
dispatch/
├── app/
│   ├── main.py                  # Point d'entrée FastAPI
│   ├── config.py                # Constantes et seuils configurables
│   │
│   ├── models/                  # Modèles de données (Pydantic)
│   │   ├── enums.py             # VehicleType, Zone, VolumeType, OrderStatus
│   │   ├── courier.py           # Courier, GpsPosition, AssignedOrder
│   │   └── order.py             # Order, Coordinates
│   │
│   ├── services/                # Logique métier pure (sans HTTP)
│   │   ├── geo.py               # Calculs géographiques (Haversine, waypoints)
│   │   ├── fleet.py             # Gestionnaire d'état de la flotte (store)
│   │   └── dispatch.py          # Moteur de scoring et d'attribution
│   │
│   └── api/                     # Couche HTTP
│       ├── schemas.py           # Schémas request / response
│       └── routes.py            # Endpoints FastAPI
│
├── tests/
│   └── test_dispatch.py         # Suite de tests pytest (14 cas)
│
├── scripts/
│   └── seed_fleet.py            # Peuple la flotte avec 8 coursiers de démo
│
└── requirements.txt
```

### Séparation des responsabilités

| Couche | Rôle | Dépendances |
|--------|------|-------------|
| `models/` | Structures de données et validation | Pydantic uniquement |
| `services/` | Logique pure, testable sans HTTP | `models/`, `config` |
| `api/` | Sérialisation HTTP, routing | `services/`, `models/` |

Cette organisation permet de tester la logique métier entièrement sans démarrer le serveur HTTP.

---

## 3. Règles métier

### 3.1 Types de véhicules et zones

Chaque coursier possède un véhicule adapté à une zone précise. **Un scooter ne peut pas sortir de sa zone.**

| Type de véhicule | Zone autorisée | Peut porter |
|---|---|---|
| `scoot_ville` | Paris intra-muros uniquement | Standard, Volume |
| `scoot_banlieue_proche` | Petite Couronne uniquement | Standard, Volume |
| `scoot_banlieue_loin` | Grande Couronne uniquement | Standard, Volume |
| `fourgon` | Grande Couronne + **Voiture toutes zones** | Standard, Volume, Voiture |

> **Règle fourgon** : Un colis de type `Voiture` (très volumineux) ne peut être transporté que par un fourgon, et ce **quelle que soit la zone**. Si un client parisien commande un colis Voiture, le fourgon de Grande Couronne peut se déplacer pour le prendre.

### 3.2 Types de volume et capacité

Les colis ont un **poids abstrait** en unités de charge :

| Type | Poids | Exemple concret |
|------|-------|-----------------|
| `Standard` | 1 unité | Petit colis, enveloppe |
| `Volume` | 2 unités | Carton encombrant |
| `Voiture` | 5 unités | Déménagement partiel, équipement |

**Capacité maximale par véhicule :**

| Véhicule | Capacité max | Exemples de remplissage |
|----------|-------------|------------------------|
| Scooters | 5 unités | 5× Standard **OU** 2× Volume + 1× Standard **OU** 2× Volume |
| Fourgon | 10 unités | 1× Voiture + 5× Standard **OU** 5× Volume |

Un coursier **dont la charge + le poids du nouveau colis dépasse sa capacité max** est automatiquement exclu du dispatch.

### 3.3 Optimisation de groupage

Si un coursier est déjà en mission mais a de la capacité restante, le moteur vérifie si son trajet actuel **passe déjà près du nouveau point de ramassage** (seuil configurable, défaut : 2 km).

Si oui → **bonus de groupage** : le score du coursier est réduit de 50%, lui donnant une priorité forte. Objectif : éviter qu'un deuxième scooter traverse le même quartier inutilement.

---

## 4. Algorithme de dispatch

### Formule de scoring

```
score = distance_base + pénalité_charge - bonus_groupage
```

**Plus le score est bas, plus le coursier est prioritaire.**

#### distance_base (km)
Distance orthodromique (formule de Haversine) entre la position GPS actuelle du coursier et le point de ramassage de la commande. C'est le facteur dominant.

```python
def haversine(p1: GpsPosition, p2: GpsPosition) -> float:
    R = 6371.0  # rayon Terre en km
    lat1, lon1 = radians(p1.lat), radians(p1.lon)
    lat2, lon2 = radians(p2.lat), radians(p2.lon)
    d_lat, d_lon = lat2 - lat1, lon2 - lon1
    a = sin(d_lat/2)**2 + cos(lat1) * cos(lat2) * sin(d_lon/2)**2
    return R * 2 * asin(sqrt(a))
```

#### pénalité_charge (km équivalents)
```
pénalité = charge_actuelle × LOAD_PENALTY_PER_UNIT   (défaut : 0.4 km/unité)
```
Un scooter portant déjà 4 colis Standard (charge = 4) reçoit une pénalité de +1.6 km dans son score, le défavorisant face à un scooter libre à 2 km.

#### bonus_groupage (km équivalents)
```python
waypoints = [position_actuelle] + [pickup, delivery de chaque course en portefeuille]
dist_min = min(haversine(wp, nouveau_ramassage) for wp in waypoints)

if dist_min <= GROUPAGE_PROXIMITY_KM:   # seuil : 2 km
    bonus = distance_base × GROUPAGE_DISCOUNT_FACTOR  # 50%
```

### Exemple concret

| Coursier | Dist. ramassage | Charge | Groupage ? | Score final |
|----------|----------------|--------|-----------|-------------|
| KEN (libre, 1.5 km) | 1.5 km | 0 unités | Non | **1.5 + 0 - 0 = 1.5** |
| THO (chargé, 1.2 km) | 1.2 km | 3 unités | Non | 1.2 + 1.2 - 0 = **2.4** |
| ALI (chargé, 3.0 km) | 3.0 km | 2 unités | Oui (passe à 800m) | 3.0 + 0.8 - 1.5 = **2.3** |

→ **KEN** est choisi (score 1.5), bien qu'il soit légèrement plus loin que THO.

---

## 5. Installation

### Prérequis

- Python 3.10 ou supérieur
- pip

### Étapes

```bash
# 1. Cloner le repo
git clone https://github.com/kenway974/dispatch.git
cd dispatch

# 2. Créer un environnement virtuel (recommandé)
python -m venv .venv

# Activer sur Windows
.venv\Scripts\activate

# Activer sur macOS/Linux
source .venv/bin/activate

# 3. Installer les dépendances
pip install -r requirements.txt
```

---

## 6. Démarrage

```bash
# Depuis la racine du projet (dossier dispatch/)
uvicorn app.main:app --reload
```

Le serveur démarre sur `http://localhost:8000`.

| Interface | URL |
|-----------|-----|
| **Swagger UI** (documentation interactive) | http://localhost:8000/docs |
| **ReDoc** (documentation lisible) | http://localhost:8000/redoc |
| **Health check** | http://localhost:8000/health |

---

## 7. API REST — Référence complète

### `GET /health` — État du système

```bash
curl http://localhost:8000/health
```

```json
{
  "status": "ok",
  "courier_count": 8,
  "order_count": 3,
  "active_couriers": 7
}
```

---

### `POST /couriers` — Enregistrer un coursier

```bash
curl -X POST http://localhost:8000/couriers \
  -H "Content-Type: application/json" \
  -d '{
    "code": "KEN",
    "vehicle_type": "scoot_ville",
    "lat": 48.8566,
    "lon": 2.3522
  }'
```

```json
{
  "code": "KEN",
  "vehicle_type": "scoot_ville",
  "lat": 48.8566,
  "lon": 2.3522,
  "is_active": true,
  "current_load": 0,
  "max_load": 5,
  "remaining_capacity": 5,
  "order_count": 0,
  "assigned_orders": []
}
```

**Valeurs acceptées pour `vehicle_type` :** `scoot_ville` · `scoot_banlieue_proche` · `scoot_banlieue_loin` · `fourgon`

---

### `POST /orders` — Soumettre une commande (déclenche le dispatch)

C'est l'endpoint principal. La commande est enregistrée **et** immédiatement attribuée au meilleur coursier.

```bash
curl -X POST http://localhost:8000/orders \
  -H "Content-Type: application/json" \
  -d '{
    "id": "ORD-001",
    "pickup_lat": 48.8559,
    "pickup_lon": 2.3578,
    "delivery_lat": 48.8864,
    "delivery_lon": 2.3432,
    "zone": "Paris",
    "volume_type": "Standard"
  }'
```

```json
{
  "success": true,
  "order_id": "ORD-001",
  "assigned_to": "KEN",
  "score": 1.243,
  "reason": "Coursier 'KEN' assigné (distance score: 1.24 km, charge: 1/5).",
  "eligible_count": 3,
  "order": {
    "id": "ORD-001",
    "zone": "Paris",
    "volume_type": "Standard",
    "status": "assigned",
    "assigned_courier": "KEN",
    ...
  }
}
```

**Valeurs acceptées pour `zone` :** `Paris` · `Petite_Couronne` · `Grande_Couronne`  
**Valeurs acceptées pour `volume_type` :** `Standard` · `Volume` · `Voiture`

**Cas d'échec** — aucun coursier éligible :
```json
{
  "success": false,
  "order_id": "ORD-002",
  "assigned_to": null,
  "score": null,
  "reason": "Aucun coursier éligible pour la zone 'Grande_Couronne' avec le volume 'Voiture'.",
  "eligible_count": 0
}
```

---

### `PUT /couriers/{code}/position` — Mettre à jour la position GPS

Appelé en continu par l'application mobile du coursier.

```bash
curl -X PUT http://localhost:8000/couriers/KEN/position \
  -H "Content-Type: application/json" \
  -d '{"lat": 48.8620, "lon": 2.3480}'
```

---

### `PUT /couriers/{code}/active?active=false` — Désactiver un coursier

```bash
# Fin de service / pause
curl -X PUT "http://localhost:8000/couriers/KEN/active?active=false"

# Retour en service
curl -X PUT "http://localhost:8000/couriers/KEN/active?active=true"
```

---

### `GET /couriers` — Liste de la flotte complète

```bash
curl http://localhost:8000/couriers
```

---

### `GET /couriers/{code}` — Détail d'un coursier

```bash
curl http://localhost:8000/couriers/KEN
```

---

### `GET /orders` — Liste toutes les commandes

```bash
curl http://localhost:8000/orders
```

---

### `GET /orders/{order_id}` — Statut d'une commande

```bash
curl http://localhost:8000/orders/ORD-001
```

---

## 8. Tests

```bash
pytest tests/ -v
```

Exemple de sortie :

```
tests/test_dispatch.py::TestGeo::test_haversine_same_point          PASSED
tests/test_dispatch.py::TestGeo::test_haversine_paris_montmartre     PASSED
tests/test_dispatch.py::TestGeo::test_haversine_symmetry             PASSED
tests/test_dispatch.py::TestGeo::test_min_distance_to_route_nearby   PASSED
tests/test_dispatch.py::TestEligibility::test_scoot_ville_eligible_for_paris              PASSED
tests/test_dispatch.py::TestEligibility::test_scoot_ville_not_eligible_for_petite_couronne PASSED
tests/test_dispatch.py::TestEligibility::test_fourgon_eligible_for_voiture_any_zone       PASSED
tests/test_dispatch.py::TestEligibility::test_courier_at_capacity_not_eligible            PASSED
tests/test_dispatch.py::TestEligibility::test_volume_colis_fits_remaining_capacity        PASSED
tests/test_dispatch.py::TestScoring::test_closer_courier_has_lower_score    PASSED
tests/test_dispatch.py::TestScoring::test_loaded_courier_penalized          PASSED
tests/test_dispatch.py::TestScoring::test_groupage_reduces_score            PASSED
tests/test_dispatch.py::TestDispatch::test_dispatch_assigns_nearest_courier        PASSED
tests/test_dispatch.py::TestDispatch::test_dispatch_petite_couronne_goes_to_right_vehicle PASSED
tests/test_dispatch.py::TestDispatch::test_dispatch_voiture_goes_to_fourgon        PASSED
tests/test_dispatch.py::TestDispatch::test_dispatch_no_eligible_courier            PASSED
tests/test_dispatch.py::TestDispatch::test_dispatch_updates_courier_load           PASSED
tests/test_dispatch.py::TestDispatch::test_dispatch_full_courier_skipped           PASSED
tests/test_dispatch.py::TestDispatch::test_dispatch_multiple_orders_sequential     PASSED

19 passed in 0.42s
```

### Ce qui est testé

| Catégorie | Cas couverts |
|-----------|-------------|
| **Géographie** | Distance nulle, distance connue Paris↔Montmartre, symétrie, détection groupage |
| **Éligibilité** | Zone correcte, zone incorrecte, Voiture→fourgon, coursier inactif, capacité pleine, capacité partielle |
| **Scoring** | Coursier plus proche favorisé, pénalité charge, bonus groupage |
| **Dispatch** | Attribution au plus proche, zonage respecté, fourgon pour Voiture, aucun éligible, charge mise à jour, coursier plein ignoré, commandes séquentielles |

---

## 9. Configuration

Tous les seuils métier sont centralisés dans `app/config.py`. Aucune modification de code logique n'est nécessaire pour ajuster le comportement.

```python
# app/config.py

# Poids des colis en unités de charge
VOLUME_WEIGHTS = {
    VolumeType.STANDARD: 1,
    VolumeType.VOLUME:   2,
    VolumeType.VOITURE:  5,
}

# Capacité max par type de véhicule
MAX_LOAD_BY_VEHICLE = {
    VehicleType.SCOOT_VILLE:            5,
    VehicleType.SCOOT_BANLIEUE_PROCHE:  5,
    VehicleType.SCOOT_BANLIEUE_LOIN:    5,
    VehicleType.FOURGON:               10,
}

# Seuil de proximité pour déclencher le bonus groupage (km)
GROUPAGE_PROXIMITY_KM = 2.0

# Réduction de score si groupage détecté (0.5 = -50%)
GROUPAGE_DISCOUNT_FACTOR = 0.5

# Pénalité par unité de charge (km équivalents)
LOAD_PENALTY_PER_UNIT = 0.4
```

---

## 10. Peupler la flotte de démo

Le script `scripts/seed_fleet.py` enregistre 8 coursiers positionnés sur des adresses réelles de Paris et banlieue :

```bash
python scripts/seed_fleet.py
```

```
Connexion à http://localhost:8000...
API en ligne. Flotte actuelle : 0 coursier(s).

  ✓ KEN (scoot_ville)             — Paris centre (Île de la Cité)
  ✓ THO (scoot_ville)             — Montmartre
  ✓ ALI (scoot_ville)             — Bastille
  ✓ MAR (scoot_banlieue_proche)   — Saint-Denis
  ✓ LEA (scoot_banlieue_proche)   — Aubervilliers
  ✓ SAM (scoot_banlieue_loin)     — Sarcelles
  ✓ FOU (fourgon)                 — Versailles
  ✓ MAX (fourgon)                 — Créteil

Flotte prête : 8 coursiers actifs.
```

---

## 11. Étendre le projet

### Remplacer le store in-memory par une base de données

Le `FleetManager` dans `app/services/fleet.py` est l'unique point de persistance. Il suffit de remplacer les deux dicts `_couriers` et `_orders` par des requêtes SQLAlchemy/Redis sans toucher au reste du code.

### Ajouter un type de véhicule

1. Ajouter la valeur dans `app/models/enums.py` → `VehicleType`
2. Ajouter sa capacité dans `app/config.py` → `MAX_LOAD_BY_VEHICLE`
3. Ajouter sa zone dans `app/config.py` → `ELIGIBLE_ZONES_BY_VEHICLE`

### Ajouter un webhook de notification

À la fin de `dispatch_order()` dans `app/services/dispatch.py`, appeler un service externe (Slack, SMS, webhook) avec le `DispatchResult`.

### Passer à un scoring plus avancé (OSRM / Google Maps)

Remplacer `haversine()` dans `app/services/geo.py` par un appel à une API de routing réelle pour obtenir des distances routières précises au lieu de distances à vol d'oiseau.
