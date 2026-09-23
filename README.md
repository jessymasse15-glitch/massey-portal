# Massey Contracts & Tax — Portail client

Ceci n'est **pas** un site vitrine. C'est une application web Flask fonctionnelle avec :

- comptes clients et personnel (client / expert / admin), mots de passe hachés, sessions sécurisées ;
- **authentification à deux facteurs (MFA/TOTP)**, obligatoire pour les comptes expert/admin, optionnelle pour les clients — compatible Google Authenticator, Authy, 1Password ;
- création réelle de dossiers, en base de données, depuis le formulaire public **et** depuis l'espace client ;
- téléversement et téléchargement de documents (fichiers stockés sous un nom interne aléatoire, jamais le nom fourni par l'utilisateur — recommandation OWASP) ;
- **signature électronique** (attestation interne avec empreinte SHA-256 du document, horodatage, adresse IP, certificat consultable — voir limites ci-dessous) ;
- **paiement en ligne** via Stripe Checkout (acompte, solde, abonnement) avec confirmation par webhook signé ;
- fil de messages par dossier, avec notes internes invisibles aux clients ;
- liste de tâches par dossier (client / Massey) ;
- statuts de dossier avec historique horodaté (le flux complet du document : demande reçue → vérification des conflits → mandat en attente → informations requises → analyse en cours → projet transmis → négociation → signature → dossier clos) ;
- contrôle d'accès strict : un client ne voit que ses propres dossiers ; expert/admin voient tout ;
- journal d'activité (connexions, dépôts, téléchargements, changements de statut, paiements, signatures) ;
- gestion des rôles côté admin.

Tout ce qui précède a été testé de bout en bout avant livraison — y compris le MFA avec de vrais codes TOTP vérifiés contre les vecteurs de test officiels RFC 4226, la signature électronique (blocage avant finalisation, verrouillage après signature, vérification d'empreinte), et le paiement Stripe simulé contre un faux serveur reproduisant l'API Stripe (création de session, webhook signé, rejet d'une signature falsifiée).

## Configuration requise pour activer paiement et MFA

Rien n'est codé en dur : sans configuration, l'app fonctionne quand même (MFA reste désactivable pour les clients, le bouton de paiement affiche "non configuré" au lieu de planter).

```bash
# .env ou variables d'environnement de l'hébergeur
SECRET_KEY=une-longue-chaine-aleatoire          # obligatoire en production
STRIPE_SECRET_KEY=sk_live_...                    # active le paiement
STRIPE_WEBHOOK_SECRET=whsec_...                  # obligatoire pour confirmer les paiements
```

Pour Stripe : crée un compte sur [stripe.com](https://stripe.com), récupère la clé secrète dans le tableau de bord, puis configure un endpoint webhook pointant vers `https://ton-domaine/webhooks/stripe` écoutant l'événement `checkout.session.completed` — Stripe te donnera alors le `STRIPE_WEBHOOK_SECRET` correspondant.

## Signature électronique — ce que c'est vraiment

Le mécanisme inclus (`signing.py`) est une **attestation interne avec piste d'audit** : le signataire tape son nom légal complet, coche deux cases de consentement explicite, et le serveur enregistre l'empreinte SHA-256 du document, l'horodatage, l'adresse IP et le user-agent. Le document est ensuite verrouillé (plus modifiable). C'est un mécanisme réel et fonctionnel, mais **ce n'est pas un service certifié comme DocuSign ou Dropbox Sign** : sa valeur probante devant un tribunal dépend du droit applicable et n'est pas garantie par un tiers de confiance externe.

**Pour passer à un vrai fournisseur certifié** (recommandé pour des contrats à forte valeur) : Dropbox Sign et DocuSign exposent tous deux une API REST simple. Il faudrait créer un compte chez l'un des deux, obtenir une clé API, et remplacer l'appel dans `sign_document()` (`app.py`) par une requête vers leur API — la structure du code (document déjà identifié par son hash, dossier, signataire) est prête pour cette bascule.

## Ce qui manque encore (et pourquoi)

- **Vérification de courriel / réinitialisation de mot de passe par courriel** : nécessite un service d'envoi de courriels (SendGrid, Postmark...) et un compte associé.
- **Chiffrement du contenu des fichiers au repos** : les fichiers sont stockés avec des noms internes aléatoires et un contrôle d'accès applicatif strict ; un chiffrement au niveau disque dépend de l'hébergeur choisi.
- **Un vrai fournisseur de signature certifié** (voir ci-dessus) — le mécanisme actuel est fonctionnel mais interne.

Ces éléments demandent un compte tiers avec ses propres identifiants, que je ne peux pas créer à ta place.

## Lancer le projet en local

```bash
cd massey-portal
python3 -m venv venv
source venv/bin/activate        # Windows : venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Le site est alors sur http://127.0.0.1:5000. La base de données SQLite se crée automatiquement au premier lancement dans `instance/massey.db`.

### Créer le premier compte administrateur

Un compte s'inscrit toujours avec le rôle `client` par défaut (formulaire public ou `/inscription`). Pour promouvoir quelqu'un `admin` ou `expert` :

```bash
flask --app app create-admin
```

(suit les invites : courriel, nom, mot de passe — crée le compte s'il n'existe pas, ou promeut un compte existant.)

Un administrateur peut ensuite changer le rôle de n'importe qui depuis `/admin/utilisateurs`.

**Important** : dès la première connexion, un compte expert ou admin est bloqué sur la page d'activation du MFA (`/compte/mfa/activer`) tant qu'il ne l'a pas configuré — c'est obligatoire, pas une suggestion.

## Déployer en production

L'application est un Flask standard, déployable sur n'importe quel hébergeur Python. Deux options simples et gratuites/pas chères pour démarrer :

### Option A — Render.com (recommandé pour démarrer)
1. Crée un dépôt GitHub avec ce dossier.
2. Sur [render.com](https://render.com), "New Web Service" → connecte le dépôt.
3. Build command : `pip install -r requirements.txt`
4. Start command : `gunicorn app:app`
5. Ajoute une variable d'environnement `SECRET_KEY` avec une valeur aléatoire longue.
6. **Important** : le disque de Render est éphémère par défaut. Ajoute un "Persistent Disk" (payant, quelques dollars/mois) monté sur `/opt/render/project/src/instance` et `/opt/render/project/src/static/uploads`, sinon la base de données et les fichiers téléversés seront effacés à chaque redéploiement.

### Option B — Railway.app
Même principe : connecter le dépôt, définir `SECRET_KEY`, ajouter un volume persistant pour `instance/` et `static/uploads/`.

### Base de données en production
SQLite convient pour démarrer (quelques dizaines de dossiers actifs), mais un seul fichier sur disque devient fragile à mesure que le volume grandit ou si tu déploies plusieurs instances. Migrer vers Postgres géré (Render/Railway en proposent un gratuit ou pas cher) est le prochain palier naturel — cela demande d'adapter `db.py` (actuellement en SQL brut, facilement portable).

## Sécurité — ce qui est déjà en place

- Mots de passe jamais stockés en clair (`werkzeug.security.generate_password_hash`).
- Sessions signées cryptographiquement (`SECRET_KEY` à définir en production, jamais la valeur par défaut de dev).
- Noms de fichiers téléversés jamais utilisés tels quels sur le disque (nom interne aléatoire, cf. OWASP).
- Extensions de fichiers autorisées limitées, taille max 15 Mo par fichier.
- Accès aux dossiers vérifié à chaque requête (`can_view_dossier`) — un client ne peut jamais charger l'URL d'un dossier qui n'est pas le sien (testé).
- Notes/messages internes filtrés côté serveur avant l'envoi au template — jamais transmis au navigateur du client.
- Journal d'activité pour les actions sensibles (connexions, téléversements, téléchargements, changements de statut, changements de rôle).

## Structure du projet

```
massey-portal/
  app.py              routes Flask (auth, MFA, portail, paiement, signature, admin)
  db.py               schéma SQL + accès SQLite
  totp.py             MFA/TOTP — pur stdlib, validé contre RFC 4226
  payments.py         intégration Stripe Checkout via API REST (sans SDK)
  signing.py          signature électronique — attestation + empreinte SHA-256
  templates/           pages Jinja2
  static/css/          styles
  static/uploads/      fichiers clients (créé au runtime, par dossier)
  requirements.txt
  Procfile             pour Render/Railway/Heroku-like
```
