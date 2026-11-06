# Stock · version cloud (Next.js + PostgreSQL)

Application d'inventaire par code-barres pour boutiques, avec un compte par boutique.

## Ce que fait cette version

- Inscription / connexion par **email + mot de passe** ou **Google**.
- Données **cloisonnées par boutique** (tables `stock_*` préfixées).
- Scan depuis n'importe quel téléphone via `/scan` (caméra).
- **Notifications Web Push** même application fermée, envoyées par un cron Vercel quotidien (`/api/cron/expiry`, 8h UTC).
- Export CSV, recherche, statistiques, alerte expiration à 7 jours.

## Variables d'environnement

Voir `.env.example`. En production, `STOCK_POSTGRES_URL` est injectée automatiquement par la connexion Neon de Vercel (préfixe `STOCK`).

## Déploiement

```bash
npm install
npm run build
vercel deploy --prod
```

La variable `NEXT_PUBLIC_APP_URL` doit contenir l'URL publique finale (ex. `https://stock-local-leniang-academy.vercel.app`).

## Google OAuth

URI de redirection à autoriser dans Google Cloud Console :

```
https://<domaine-vercel>/api/auth/google/callback
```

Les « origines JavaScript autorisées » ne sont pas nécessaires : le flux OAuth est géré côté serveur.

## Cron

`vercel.json` déclare un cron quotidien à 8h UTC. Sur Vercel Hobby, un seul passage par jour est autorisé.
