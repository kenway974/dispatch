# Deploy Checklist — Railway

## ✅ Déjà prêt (avant cette session)

| Fichier | Statut |
|---|---|
| `Procfile` | ✅ Correct — `uvicorn app.main:app --host 0.0.0.0 --port $PORT` |
| `requirements.txt` | ✅ Présent — FastAPI, Uvicorn, Pydantic, Jinja2, etc. |
| `.gitignore` | ✅ Complet — `.env`, `__pycache__/`, `*.py[cod]`, `venv/`, `.venv/`, `dist/`, `build/` |
| `app/main.py` | ✅ Point d'entrée FastAPI valide |

---

## 🆕 Créé lors de cette session

| Fichier | Description |
|---|---|
| `railway.toml` | Config Railway : builder Nixpacks, startCommand depuis Procfile, restart on failure |
| `.env.example` | Template des variables d'env (aucune requise pour l'instant) |

---

## 🔧 Variables d'environnement à configurer dans Railway UI

> **Settings → Variables** dans le dashboard Railway

| Variable | Obligatoire | Valeur |
|---|---|---|
| `PORT` | ❌ Non | Injecté automatiquement par Railway — **ne pas définir** |
| `DISPATCH_DB_PATH` | ⚠️ Pour l'essai | `/data/pilote.db` — chemin de la base SQLite du mode pilote |
| `DISPATCH_IMPORT_TOKEN` | Si import des positions | Jeton partagé protégeant `POST /positions/import`. Non défini = import refusé |

Les règles de dispatch elles-mêmes restent des constantes Python dans `app/config.py`.

---

## 💾 Volume persistant — **indispensable pour l'essai d'un mois**

Le système de fichiers de Railway est **éphémère** : à chaque redéploiement, il repart
de zéro. Sans volume, le journal des comparaisons et la flotte disparaissent — un mois
d'essai perdu au premier `git push`.

1. **Settings → Volumes → New Volume**
2. Mount path : `/data`
3. **Settings → Variables** → ajouter `DISPATCH_DB_PATH=/data/pilote.db`
4. Redéployer

Vérification après déploiement : ajouter un coursier, redéployer, puis recharger
`/pilote`. Le coursier doit toujours être là.

Sauvegarde recommandée pendant l'essai : télécharger régulièrement
`https://<projet>.up.railway.app/pilote/journal/export.csv`.

---

## 📋 Étapes manuelles restantes

### 1. Pousser le code sur GitHub (si pas encore fait)
```bash
git add .
git commit -m "chore: add railway.toml and .env.example"
git push origin main
```

### 2. Créer le projet sur Railway
1. Aller sur [railway.app](https://railway.app) → **New Project**
2. Choisir **Deploy from GitHub repo**
3. Sélectionner ce dépôt
4. Railway détecte automatiquement `railway.toml` + `Procfile`

### 3. Vérifier le déploiement
- Onglet **Deployments** → logs en temps réel
- Chercher : `Application startup complete` dans les logs Uvicorn
- Tester l'URL publique générée :
  - `https://<projet>.up.railway.app/docs` — l'API
  - `https://<projet>.up.railway.app/` — la démo prospect
  - `https://<projet>.up.railway.app/pilote` — le mode pilote (l'essai)

### 4. (Optionnel) Domaine custom
- **Settings → Domains** → ajouter votre domaine
- Configurer le CNAME chez votre registrar

---

## 🗂 Structure du projet

```
dispatch/
├── app/
│   ├── api/          # Routes REST + UI + mode pilote
│   ├── models/       # Modèles Pydantic + enums
│   ├── services/     # dispatch, fleet, geo, comparaison, storage
│   ├── templates/    # Jinja2 (index.html, pilote.html)
│   ├── config.py     # Constantes métier
│   └── main.py       # Point d'entrée FastAPI
├── tests/
├── scripts/          # seed_fleet.py
├── Procfile          ✅
├── railway.toml      🆕
├── requirements.txt  ✅
├── .env.example      🆕
└── .gitignore        ✅
```
