# Sécurité

Adhérents contient des données personnelles, dont celles de mineurs, des coordonnées bancaires, et sert de serveur de connexion à d'autres applis. Une faille y est donc prise au sérieux.

## Signaler une faille

**N'ouvrez pas d'issue publique.** Passez par le signalement privé de GitHub : onglet **Security** du dépôt, puis **Report a vulnerability**. Seuls les mainteneurs voient le signalement.

Indiquez si possible :

- ce que la faille permet (lire des données, se connecter à la place de quelqu'un, etc.) ;
- les étapes pour la reproduire, et la version ou le commit concerné ;
- la configuration utile : derrière un reverse proxy ou non, appli cliente OIDC utilisée.

N'utilisez jamais de vraies données d'adhérents pour une démonstration : une installation locale avec des données fictives suffit (voir [Développement](docs/developpement.md)).

## Ce qui est concerné

- La connexion, les sessions, la double authentification et les liens à usage unique.
- Le serveur OIDC : autorisation, jetons, consentement, déconnexion des applis.
- Les droits : une page ou une donnée accessible sans le rôle qui devrait l'ouvrir.
- Le chiffrement et la lecture des IBAN.
- L'espace adhérent : une famille qui verrait un autre foyer.

Seule la branche `main` est suivie : il n'y a pas encore de version publiée.

## Si vous hébergez l'appli

Les protections en place et leurs limites sont décrites dans [Architecture](docs/architecture.md#sécurité). La marche à suivre en cas d'incident (clé compromise, fuite d'une sauvegarde) est dans [Installation et exploitation](docs/installation.md#incident-de-sécurité).
