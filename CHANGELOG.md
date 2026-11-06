# Journal des versions

Les changements notables du projet sont consignés ici. Le format s'inspire de [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/).

## 0.1.0 - 2026-11-05 : phase 0, le socle

### Ajouté

- **Socle repris de l'Inventaire** : interface (barre latérale, onglets sur mobile, thèmes, 7 palettes, taille du texte), connexion, sessions révocables, Mon compte, notifications, emails, journal, rôles et droits, personnalisation, page d'état de l'installation, Docker.
- **Modèle de compte séparé de la personne** : `Account` ne décrit que la connexion et porte un `sub` opaque, prêt pour le serveur OIDC.
- **Commande `flask create-admin`**, qui sert aussi d'accès de secours.
- **Commande `flask run-tasks`** pour les tâches périodiques : purges et envoi des emails.
- **Suite de tests** : balayage de toutes les routes par rôle, migrations sur une base remplie, et parcours dans un vrai navigateur.
- **Documentation** et plan du projet.

### Corrigé par rapport à l'Inventaire

- **Aucun JavaScript en ligne** : les 43 gestionnaires `onclick`, `onsubmit` et `onchange` sont remplacés par `data-confirm`, `data-autosubmit` et `data-open-nav`, gérés dans `app.js`.
- **CSP bloquante par défaut**, et non plus en simple observation.
- **Thème sombre écrit une seule fois**, dans `dark.css`, au lieu de deux blocs à garder identiques.
- **Limitation des tentatives en base**, commune à tous les workers. Elle survit à un redémarrage et ne conserve qu'une empreinte de l'email ou de l'adresse IP.
- **Cookies à noms propres** (`adherents_session`, `adherents_remember`), pour ne pas entrer en conflit avec l'Inventaire sur `localhost`.
- **Champs sensibles masqués au journal** : `activity.diff()` note qu'un IBAN ou un mot de passe a changé, jamais sa valeur.
