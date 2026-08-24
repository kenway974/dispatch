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
8. [Mode pilote — essai en conditions réelles](#8-mode-pilote--essai-en-conditions-réelles)
9. [Tests](#9-tests)
10. [Configuration](#10-configuration)
11. [Peupler la flotte de démo](#11-peupler-la-flotte-de-démo)
12. [Étendre le projet](#12-étendre-le-projet)

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
│   ├── main.py                  # Point d'entrée FastAPI + restauration de l'état
│   ├── config.py                # Constantes et seuils configurables
│   │
│   ├── models/                  # Modèles de données (Pydantic)
│   │   ├── enums.py             # VehicleType, Zone, VolumeType, ClientTier, OrderStatus
│   │   ├── coursier.py          # Coursier, GpsPosition, AssignedOrder
│   │   └── order.py             # Order, Coordinates
│   │
│   ├── services/                # Logique métier pure (sans HTTP)
│   │   ├── geo.py               # Calculs géographiques (Haversine, waypoints)
│   │   ├── fleet.py             # Gestionnaire d'état de la flotte (store)
│   │   ├── dispatch.py          # Moteur de scoring et d'attribution
│   │   ├── comparaison.py       # Mode pilote : classement expliqué et verdict
│   │   └── storage.py           # Persistance SQLite (journal + instantané flotte)
│   │
│   ├── templates/
│   │   ├── index.html           # Démo prospect
│   │   └── pilote.html          # Interface de pilotage (essai d'un mois)
│   │
│   └── api/                     # Couche HTTP
│       ├── schemas.py           # Schémas request / response
│       ├── routes.py            # Endpoints REST
│       ├── routes_ui.py         # Démo : page, imports CSV/Excel, géocodage
│       └── routes_pilote.py     # Mode pilote : comparaison, journal, export
│
├── tests/
│   ├── test_dispatch.py         # Moteur de dispatch (25 cas)
│   └── test_pilote.py           # Mode pilote et persistance (35 cas)
│
├── scripts/
│   └── seed_fleet.py            # Peuple la flotte avec les coursiers de démo
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

### `POST /coursiers` — Enregistrer un coursier

```bash
curl -X POST http://localhost:8000/coursiers \
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

### `PUT /coursiers/{code}/position` — Mettre à jour la position GPS

Appelé en continu par l'application mobile du coursier.

```bash
curl -X PUT http://localhost:8000/coursiers/KEN/position \
  -H "Content-Type: application/json" \
  -d '{"lat": 48.8620, "lon": 2.3480}'
```

---

### `PUT /coursiers/{code}/active?active=false` — Désactiver un coursier

```bash
# Fin de service / pause
curl -X PUT "http://localhost:8000/coursiers/KEN/active?active=false"

# Retour en service
curl -X PUT "http://localhost:8000/coursiers/KEN/active?active=true"
```

---

### `GET /coursiers` — Liste de la flotte complète

```bash
curl http://localhost:8000/coursiers
```

---

### `GET /coursiers/{code}` — Détail d'un coursier

```bash
curl http://localhost:8000/coursiers/KEN
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

## 8. Mode pilote — essai en conditions réelles

Interface : **`http://localhost:8000/pilote`**

Le mode pilote sert à répondre à une seule question, avec des chiffres :
*est-ce que ce moteur décide comme notre dispatcheur ?*

### Le protocole

Le dispatcheur travaille normalement. Pour chaque course, il saisit le coursier
qu'il **vient d'attribuer**, puis l'application révèle ce qu'elle aurait décidé.

L'ordre compte. Révéler d'abord la réponse du moteur biaiserait le choix humain
et l'essai ne vaudrait plus rien — c'est pourquoi l'interface exige le choix
manuel avant d'afficher quoi que ce soit.

### Deux règles structurantes

**1. C'est le choix humain qui est appliqué à la flotte, jamais celui du moteur.**
Le dispatcheur reste maître de son exploitation ; l'application se contente
d'observer. Sans cette règle, l'état simulé divergerait du terrain dès le premier
désaccord, et toutes les comparaisons suivantes seraient faussées.

**2. Chaque comparaison est journalisée avec le classement complet.**
Un désaccord de la deuxième semaine reste analysable à la fin du mois : on sait
qui était éligible, à quel score, et pourquoi les autres ont été écartés.

### Déroulé d'une journée

| Étape | Action |
|-------|--------|
| Début de service | Renseigner la position de chaque coursier et **les courses qu'il porte déjà** (`＋` sur sa fiche) |
| Nouvelle course | Saisir ramassage / livraison, volume, client, délai |
| Attribution | Choisir le coursier retenu, puis **Comparer et journaliser** |
| Livraison | Cliquer `✕` sur la course pour libérer la charge |
| Fin de mois | **Export CSV** — le livrable de l'essai |

Sans les courses déjà en portefeuille, le moteur croit tout le monde disponible
et son équilibrage de charge ne veut plus rien dire. C'est la saisie à ne pas sauter.

### Où sont les coursiers ?

Le moteur note les coursiers sur leur distance au point de ramassage. Cette note
ne vaut donc rien de plus que la position sur laquelle elle est calculée — et
personne ne ressaisira huit positions à la main entre deux courses.

Trois sources, par ordre de préférence :

| Source | Mise en œuvre | Fraîcheur |
|--------|---------------|-----------|
| **Import** depuis le système de suivi déjà en place | `POST /positions/import` alimenté par `scripts/sync_positions.py` | temps réel |
| **Estimation à l'estime** | aucune — actif par défaut | recalculée en continu |
| **Clic sur la carte** | le dispatcheur reporte ce qu'il voit dans l'application de la société | instantanée, manuelle |

**L'import est la voie normale.** L'entreprise dispose déjà d'une application sur
laquelle les coursiers ouvrent leur shift et qui donne leur position en direct.
Le moteur n'a pas à refaire ce travail, il a besoin d'y accéder :
`POST /positions/import` est la prise unique par laquelle ces positions entrent,
quelle que soit leur provenance. Voir `scripts/sync_positions.py` — tout est
écrit sauf la fonction qui interroge le système source, à compléter une fois son
API connue.

**L'estimation prend le relais** dès qu'aucune position récente n'est disponible :
dernier point connu, temps écoulé, vitesse moyenne du véhicule, suite du trajet
déjà assigné. C'est le raisonnement que le dispatcheur fait de tête ; il est ici
simplement écrit. Aucune intégration nécessaire — l'essai peut démarrer sans que
personne n'ouvre l'accès à quoi que ce soit.

Deux garanties, sans lesquelles ces chiffres ne vaudraient rien :

1. **Une estimation n'est jamais écrite dans l'état.** La position stockée reste
   le dernier point réellement connu ; l'estimation est recalculée à chaque
   lecture. Sinon l'erreur s'accumulerait — une estimation d'estimation
   d'estimation — et au bout d'une heure le moteur raisonnerait sur une fiction.

2. **La fraîcheur est affichée, toujours.** « GPS il y a 20 s » et « estimée
   depuis sa livraison d'il y a 35 min » ne se valent pas : la seconde mérite un
   coup de téléphone avant de suivre la recommandation. Chaque coursier porte son
   badge, et la carte le colore en conséquence (vert temps réel, orange estimé,
   rouge périmé au-delà de 20 minutes).

Vitesses moyennes et seuils de péremption sont dans `app/config.py` — ce sont des
ordres de grandeur urbains à ajuster après les premiers jours d'essai.

```bash
# Activer l'import (fermé par défaut : sans jeton configuré, l'endpoint refuse)
DISPATCH_IMPORT_TOKEN=<jeton partagé avec le script de synchronisation>
```

Une page `/suivi/{code}` existe aussi : le coursier l'ouvre sur son téléphone et
elle envoie sa position toutes les 30 secondes, sans rien installer. Elle fait
doublon avec l'application de la société et n'est là qu'en secours, si aucun
accès technique à cette dernière n'est obtenu.

### Les indicateurs

| Indicateur | Ce qu'il dit |
|------------|--------------|
| **Taux d'accord** | Part des courses où le moteur et le dispatcheur ont choisi le même coursier |
| **Top 3** | Part des cas où le choix humain figurait dans les trois premiers du moteur — un désaccord sur le 2e n'a pas le poids d'un écart total |
| **Écart moyen** | Coût moyen d'un désaccord, en km équivalents |
| **Répartition par coursier** | Qui reçoit plus (ou moins) selon le moteur. Un écart systématique sur un coursier vaut souvent un réglage, pas un rejet |

Un taux d'accord de 100 % signifierait que le moteur n'apporte rien. L'intérêt
est dans les désaccords : chacun est soit une erreur du moteur à corriger, soit
une optimisation que l'humain n'avait pas vue.

### Endpoints

| Méthode | Route | Rôle |
|---------|-------|------|
| `POST` | `/pilote/comparaison` | Journalise la décision et révèle celle du moteur |
| `POST` | `/pilote/simulation` | Simulation à blanc — rien n'est journalisé ni attribué |
| `GET` | `/pilote/journal` | Journal + statistiques cumulées |
| `DELETE` | `/pilote/journal/{id}` | Supprime une saisie erronée |
| `GET` | `/pilote/journal/export.csv` | Export CSV de l'essai |
| `POST` | `/coursiers/{code}/courses` | Déclare une course déjà en portefeuille |
| `DELETE` | `/coursiers/{code}/courses/{id}` | Course livrée — libère la charge |
| `POST` | `/positions/import` | Reprise des positions du système de l'entreprise (jeton requis) |
| `GET` | `/pilote/positions` | Positions exploitables et leur fraîcheur |
| `POST` | `/coursiers/{code}/ping` | Position remontée par un téléphone (secours) |

Exemple :

```bash
curl -X POST http://localhost:8000/pilote/comparaison \
  -H "Content-Type: application/json" \
  -d '{
    "pickup_lat": 48.8566, "pickup_lon": 2.3522,
    "delivery_lat": 48.8864, "delivery_lon": 2.3432,
    "zone": "Paris", "volume_type": "Standard",
    "choix_manuel": "MEH",
    "commentaire": "il rentrait au dépôt"
  }'
```

```json
{
  "choix_manuel": "MEH",
  "choix_app": "KEN",
  "accord": false,
  "rang_manuel": 2,
  "ecart_km": 2.18,
  "verdict": "Désaccord : l'application aurait pris KEN. MEH arrive 2e de son classement, à 2.2 km équivalents.",
  "classement": [ "..." ],
  "statistiques": { "total": 12, "taux_accord": 75.0 }
}
```

### Persistance

L'essai dure un mois : le journal ne doit pas disparaître à un redéploiement.
Tout est stocké en SQLite (bibliothèque standard, aucune dépendance ajoutée) —
le journal des comparaisons **et** un instantané de la flotte, réécrit à chaque
mutation et rechargé au démarrage.

```bash
DISPATCH_DB_PATH=/data/pilote.db   # défaut : data/pilote.db
```

Sur Railway, monter un volume et pointer `DISPATCH_DB_PATH` dessus : sans volume,
le système de fichiers est éphémère et le mois d'essai part au premier redéploiement.

---

## 9. Tests

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

## 10. Configuration

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

## 11. Peupler la flotte de démo

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

## 12. Étendre le projet

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
