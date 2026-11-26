# TP Week-end — Projet EKS en autonomie

Déployer l'application **atelier-photo** sur **Amazon EKS Auto Mode** avec Terraform : réseau, base PostgreSQL Multi-AZ, Pod Identity et manifestes Kubernetes générés par Terraform.

| Contenu | Description |
|---|---|
| [`docs/index.html`](docs/index.html) | **L'énoncé** : contexte, architecture, phases, points de contrôle, livrables, barème |
| [`atelier-photo/`](atelier-photo/) | **L'application à déployer** : code Python, `Dockerfile` et README (variables d'environnement, sondes, droits IAM) |

## Pour commencer

1. Ouvrir l'énoncé `docs/index.html` dans un navigateur.
2. Lire le [README de l'application](atelier-photo/README.md) : il liste les variables d'environnement que vos manifestes devront fournir.
3. Créer votre propre dépôt `tp-weekend-<prenom>` pour le rendu : aucun code Terraform n'est fourni, c'est à vous de l'écrire.

> 💶 Pensez au `terraform destroy` à la fin de chaque session de travail.
