# Atelier Photo

Petite application web de galerie photo, à déployer sur Amazon EKS.

- Les **photos** sont stockées dans **Amazon S3**.
- Les **métadonnées** (nom, taille, date, pod qui a reçu l'envoi) sont stockées dans **PostgreSQL** (Amazon RDS). La table est créée automatiquement au premier accès.
- Le **mot de passe de la base** est lu dans **AWS Secrets Manager**, au format du secret généré par RDS (`{"username": "...", "password": "..."}`).
- L'application n'utilise **aucune clé d'accès AWS** : elle prend les identifiants fournis par l'environnement (EKS Pod Identity sur le cluster).

## Variables d'environnement

| Variable | Obligatoire | Exemple | Rôle |
|---|---|---|---|
| `AWS_REGION` | oui | `eu-west-3` | Région des services AWS |
| `BUCKET_NAME` | oui | `photos-houcem` | Bucket S3 des photos |
| `DB_HOST` | oui | `xxxx.eu-west-3.rds.amazonaws.com` | Adresse de la base (sans le port) |
| `DB_PORT` | non | `5432` | Port de la base (défaut : `5432`) |
| `DB_NAME` | non | `atelier` | Nom de la base (défaut : `atelier`) |
| `DB_SECRET_ARN` | oui | `arn:aws:secretsmanager:...:secret:rds!db-...` | ARN du secret contenant l'utilisateur et le mot de passe |
| `DB_SSLMODE` | non | `require` | Mode SSL PostgreSQL (défaut : `require`) |
| `MAX_UPLOAD_MB` | non | `10` | Taille maximale d'une photo (défaut : 10 Mo) |

La base indiquée par `DB_NAME` doit exister. Sur RDS, créez-la avec l'instance (argument du nom de base initiale).

## Port et sondes

L'application écoute sur le **port 8080**.

| Chemin | Usage |
|---|---|
| `/` | Galerie et formulaire d'envoi |
| `/health` | **Liveness** : répond `200` tant que le processus tourne. Ne teste aucune dépendance. |
| `/ready` | **Readiness** : `200` si la base et le bucket sont joignables, sinon `503` avec le détail. |
| `/info` | **Diagnostic** : nom du pod, identité AWS utilisée (doit être le rôle Pod Identity), bucket, base. |

Exemple de réponse de `/info` sur le cluster :

```json
{
  "pod": "atelier-photo-7d9c8b6f4-x2kqp",
  "aws_identity": "arn:aws:sts::123456789012:assumed-role/role-pod-atelier-houcem/eks-...",
  "pod_identity_active": true,
  "bucket": "photos-houcem",
  "db_host": "xxxx.eu-west-3.rds.amazonaws.com",
  "db_name": "atelier",
  "db_secret_arn": "arn:aws:secretsmanager:eu-west-3:123456789012:secret:rds!db-..."
}
```

## Droits IAM nécessaires

| Service | Actions | Ressource |
|---|---|---|
| Secrets Manager | `secretsmanager:GetSecretValue` | le secret de la base uniquement |
| S3 | `s3:PutObject`, `s3:GetObject`, `s3:DeleteObject` | `arn:aws:s3:::<bucket>/*` |
| S3 | `s3:ListBucket` | `arn:aws:s3:::<bucket>` (utilisé par `/ready`) |

`sts:GetCallerIdentity` (utilisé par `/info`) ne demande aucun droit.

## Construire et pousser l'image

Les nœuds EKS sont en **x86_64**. Depuis un Mac Apple Silicon, précisez la plateforme :

```bash
docker build --platform linux/amd64 -t atelier-photo:eks-v1 .

# Connexion à ECR puis envoi (remplacer <compte> et la région)
aws ecr get-login-password --region eu-west-3 \
  | docker login --username AWS --password-stdin <compte>.dkr.ecr.eu-west-3.amazonaws.com

docker tag atelier-photo:eks-v1 <compte>.dkr.ecr.eu-west-3.amazonaws.com/atelier-photo:eks-v1
docker push <compte>.dkr.ecr.eu-west-3.amazonaws.com/atelier-photo:eks-v1
```

## Tester en local (facultatif)

Pour tester sans Secrets Manager, les variables `DB_USER` et `DB_PASSWORD` remplacent `DB_SECRET_ARN` :

```bash
docker run -d --name pg -e POSTGRES_PASSWORD=atelier -e POSTGRES_DB=atelier -p 5432:5432 postgres:16

docker run --rm -p 8080:8080 \
  -e AWS_REGION=eu-west-3 -e BUCKET_NAME=<un-bucket-de-test> \
  -e DB_HOST=host.docker.internal -e DB_USER=postgres -e DB_PASSWORD=atelier -e DB_SSLMODE=disable \
  -v ~/.aws:/home/appuser/.aws:ro -e AWS_PROFILE=<ton-profil> -e HOME=/home/appuser \
  atelier-photo:eks-v1
```

Puis ouvrez http://localhost:8080.

> ⚠️ `DB_USER` / `DB_PASSWORD` servent **uniquement** au test local. Sur le cluster, utilisez `DB_SECRET_ARN`.
