# Demande d'accès aux positions des coursiers

Document à transmettre au responsable, qui le relaiera au prestataire ayant
développé l'application de la société.

**L'essai ne dépend pas de cette demande.** Le moteur sait estimer les positions
sans aucune intégration (dernier point connu, temps écoulé, tournée en cours).
Cet accès améliore la précision ; il ne conditionne pas le démarrage. C'est le
premier point à dire, sinon la demande devient un blocage dans l'esprit de
celui qui la reçoit.

---

## 1. Pour le responsable — trois lignes

> Pour l'essai du moteur de dispatch, on aurait besoin de lire la position des
> coursiers en service, celle que le dispatch voit déjà sur notre application.
> Il s'agit d'une **lecture seule**, de quatre informations par coursier, rien
> de plus que ce qui est déjà affiché à l'écran aujourd'hui.
>
> Concrètement : une adresse web que notre outil interroge toutes les minutes.
> Pour le prestataire, c'est de l'ordre d'une demi-journée. La fiche technique
> ci-dessous lui donne tout ce dont il a besoin.
>
> Sans cet accès l'essai a quand même lieu — le moteur estimera les positions.
> Elles seront simplement moins précises, et les résultats de l'essai un peu
> moins nets.

---

## 2. Pour le prestataire — fiche technique

### Ce qui est demandé

Un point d'accès HTTP en **lecture seule** listant les coursiers actuellement en
service, avec leur dernière position connue.

```
GET /api/<au choix>/coursiers-en-service
Authorization: Bearer <jeton dédié, révocable>
```

Réponse attendue (la forme exacte importe peu, on s'adapte) :

```json
{
  "coursiers": [
    {
      "code": "KEN",
      "lat": 48.8566,
      "lon": 2.3522,
      "position_datee_le": "2026-08-24T09:41:12+02:00"
    }
  ]
}
```

### Les quatre champs, et pourquoi chacun

| Champ | Rôle |
|-------|------|
| `code` | Le code coursier déjà utilisé au quotidien (KEN, JC…). Sert à faire le lien, rien d'autre. |
| `lat` / `lon` | La position. |
| `position_datee_le` | **L'instant de la mesure**, pas celui de la réponse. Indispensable : dater de « maintenant » une position relevée il y a dix minutes ferait passer pour fiable une recommandation qui ne l'est pas. C'est le champ le plus important après la position elle-même. |

Ne sont demandés ni le nom, ni le téléphone, ni l'historique des déplacements,
ni quoi que ce soit hors des heures de service. Uniquement les coursiers ayant
ouvert leur shift, uniquement pendant qu'il est ouvert.

### Contraintes

| Point | Valeur |
|-------|--------|
| Méthode | `GET` uniquement — aucune écriture, aucune modification |
| Fréquence d'appel | 1 requête par minute, depuis une seule adresse IP |
| Authentification | Un jeton dédié à cet usage, révocable à tout moment |
| Durée | Le temps de l'essai (un mois), reconductible ou coupé sans préavis |
| Volume | 8 coursiers, soit une réponse de quelques centaines d'octets |

### Solutions de repli, par ordre d'effort décroissant

Si un point d'accès dédié est trop lourd à mettre en place :

1. **Un export périodique** — un fichier CSV ou JSON déposé toutes les minutes
   sur un espace accessible (S3, FTP, URL statique). Même contenu, même usage.
2. **Un accès en lecture à la base**, restreint à la table des positions.
3. **Rien.** L'essai se fait alors sur l'estimation, et le dispatcheur corrige
   au clic quand il juge qu'une position a trop dérivé.

---

## 3. Côté moteur de dispatch — déjà prêt

Rien n'est à développer de notre côté une fois l'accès obtenu :

- `POST /positions/import` reçoit les positions, protégé par un jeton partagé
  (`DISPATCH_IMPORT_TOKEN`). Fermé par défaut.
- `scripts/sync_positions.py` fait le pont : il interroge la source, traduit et
  pousse, en boucle. **Seule la fonction `recuperer_positions_source()` reste à
  compléter** — quelques lignes une fois la forme de la réponse connue.
- Un code coursier inconnu du moteur n'interrompt pas le lot : le système de la
  société suit toute la flotte, l'essai n'en couvre qu'une partie.

Délai de mise en service une fois l'accès fourni : **moins d'une journée**.
