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

**Aucune variable d'environnement applicative n'est requise** pour le déploiement initial.  
Le projet utilise uniquement des constantes Python dans `app/config.py`.

Si vous ajoutez une base de données ou une API externe, ajoutez les variables dans Railway UI **et** dans `.env.example`.

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
- Tester l'URL publique générée : `https://<projet>.up.railway.app/docs`

### 4. (Optionnel) Domaine custom
- **Settings → Domains** → ajouter votre domaine
- Configurer le CNAME chez votre registrar

---

## 🗂 Structure du projet

```
dispatch/
├── app/
│   ├── api/          # Routes REST + UI
│   ├── models/       # Modèles Pydantic + enums
│   ├── services/     # Logique dispatch, fleet, geo
│   ├── templates/    # Jinja2 (index.html)
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
