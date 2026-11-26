"""
Atelier Photo — petite application web de galerie photo.

- Les photos sont stockées dans Amazon S3.
- Les métadonnées (nom, taille, date) sont stockées dans PostgreSQL (Amazon RDS).
- Le mot de passe de la base est lu dans AWS Secrets Manager.
- Aucune clé AWS : les identifiants viennent de la chaîne par défaut de boto3
  (EKS Pod Identity, rôle IAM, profil local...).
"""

import json
import logging
import os
import socket
import uuid
from functools import lru_cache

import boto3
import psycopg
from botocore.exceptions import BotoCoreError, ClientError
from flask import Flask, abort, flash, jsonify, redirect, render_template, request, url_for

# ---------------------------------------------------------------------------
# Configuration (variables d'environnement)
# ---------------------------------------------------------------------------
AWS_REGION = os.environ.get("AWS_REGION", "eu-west-3")
BUCKET_NAME = os.environ.get("BUCKET_NAME", "")
DB_HOST = os.environ.get("DB_HOST", "")
DB_PORT = int(os.environ.get("DB_PORT", "5432"))
DB_NAME = os.environ.get("DB_NAME", "atelier")
DB_SECRET_ARN = os.environ.get("DB_SECRET_ARN", "")
DB_SSLMODE = os.environ.get("DB_SSLMODE", "require")
# Uniquement pour un test en local, sans Secrets Manager :
DB_USER = os.environ.get("DB_USER", "")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")

MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "10"))
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webp"}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("atelier-photo")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024
app.secret_key = os.environ.get("FLASK_SECRET_KEY", uuid.uuid4().hex)


# ---------------------------------------------------------------------------
# Clients AWS
# ---------------------------------------------------------------------------
@lru_cache(maxsize=None)
def aws_client(service):
    return boto3.client(service, region_name=AWS_REGION)


@lru_cache(maxsize=1)
def db_credentials():
    """Retourne (utilisateur, mot de passe) : Secrets Manager, ou variables locales."""
    if DB_SECRET_ARN:
        secret = aws_client("secretsmanager").get_secret_value(SecretId=DB_SECRET_ARN)
        data = json.loads(secret["SecretString"])
        return data["username"], data["password"]
    if DB_USER and DB_PASSWORD:
        return DB_USER, DB_PASSWORD
    raise RuntimeError("Ni DB_SECRET_ARN, ni DB_USER/DB_PASSWORD ne sont définis")


def db_connect():
    user, password = db_credentials()
    return psycopg.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=user,
        password=password,
        sslmode=DB_SSLMODE,
        connect_timeout=5,
    )


def init_db():
    with db_connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS photos (
                id          UUID PRIMARY KEY,
                filename    TEXT NOT NULL,
                s3_key      TEXT NOT NULL,
                size_bytes  INTEGER NOT NULL,
                uploaded_by TEXT NOT NULL,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    log.info("Table 'photos' prête")


_db_ready = False


def ensure_db():
    global _db_ready
    if not _db_ready:
        init_db()
        _db_ready = True


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    photos, error = [], None
    try:
        ensure_db()
        with db_connect() as conn:
            rows = conn.execute(
                "SELECT id, filename, s3_key, size_bytes, uploaded_by, created_at "
                "FROM photos ORDER BY created_at DESC LIMIT 50"
            ).fetchall()
        s3 = aws_client("s3")
        for pid, filename, key, size, pod, created in rows:
            url = s3.generate_presigned_url(
                "get_object", Params={"Bucket": BUCKET_NAME, "Key": key}, ExpiresIn=3600
            )
            photos.append(
                {"id": pid, "filename": filename, "url": url, "size_kb": round(size / 1024),
                 "pod": pod, "created": created.strftime("%d/%m/%Y %H:%M")}
            )
    except Exception as exc:  # affiché sur la page pour aider au diagnostic
        log.exception("Erreur lors du chargement de la galerie")
        error = f"{type(exc).__name__} : {exc}"

    return render_template(
        "index.html", photos=photos, error=error, pod=socket.gethostname(),
        bucket=BUCKET_NAME, db_host=DB_HOST, max_mb=MAX_UPLOAD_MB,
    )


@app.route("/upload", methods=["POST"])
def upload():
    file = request.files.get("photo")
    if not file or file.filename == "":
        flash("Aucun fichier sélectionné.", "error")
        return redirect(url_for("index"))
    if not allowed_file(file.filename):
        flash("Format non accepté (jpg, jpeg, png, gif, webp).", "error")
        return redirect(url_for("index"))

    photo_id = uuid.uuid4()
    ext = file.filename.rsplit(".", 1)[1].lower()
    key = f"photos/{photo_id}.{ext}"
    data = file.read()

    try:
        ensure_db()
        aws_client("s3").put_object(
            Bucket=BUCKET_NAME, Key=key, Body=data,
            ContentType=file.mimetype or "application/octet-stream",
        )
        with db_connect() as conn:
            conn.execute(
                "INSERT INTO photos (id, filename, s3_key, size_bytes, uploaded_by) "
                "VALUES (%s, %s, %s, %s, %s)",
                (photo_id, file.filename, key, len(data), socket.gethostname()),
            )
        flash(f"Photo « {file.filename} » enregistrée.", "ok")
        log.info("Photo %s envoyée dans s3://%s/%s", file.filename, BUCKET_NAME, key)
    except Exception as exc:
        log.exception("Échec de l'envoi")
        flash(f"Échec de l'envoi : {type(exc).__name__} : {exc}", "error")
    return redirect(url_for("index"))


@app.route("/photos/<uuid:photo_id>/delete", methods=["POST"])
def delete(photo_id):
    try:
        with db_connect() as conn:
            row = conn.execute("SELECT s3_key FROM photos WHERE id = %s", (photo_id,)).fetchone()
            if not row:
                abort(404)
            aws_client("s3").delete_object(Bucket=BUCKET_NAME, Key=row[0])
            conn.execute("DELETE FROM photos WHERE id = %s", (photo_id,))
        flash("Photo supprimée.", "ok")
    except Exception as exc:
        log.exception("Échec de la suppression")
        flash(f"Échec de la suppression : {type(exc).__name__} : {exc}", "error")
    return redirect(url_for("index"))


# ---------------------------------------------------------------------------
# Sondes et diagnostic
# ---------------------------------------------------------------------------
@app.route("/health")
def health():
    """Liveness : le processus répond. Ne teste aucune dépendance."""
    return jsonify(status="ok"), 200


@app.route("/ready")
def ready():
    """Readiness : la base et le bucket sont joignables."""
    checks = {}
    try:
        with db_connect() as conn:
            conn.execute("SELECT 1")
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"erreur : {type(exc).__name__}"
    try:
        aws_client("s3").head_bucket(Bucket=BUCKET_NAME)
        checks["s3"] = "ok"
    except (BotoCoreError, ClientError) as exc:
        checks["s3"] = f"erreur : {type(exc).__name__}"
    ok = all(v == "ok" for v in checks.values())
    return jsonify(status="ok" if ok else "ko", checks=checks), (200 if ok else 503)


@app.route("/info")
def info():
    """Diagnostic : quel pod répond, avec quelle identité AWS."""
    try:
        identity = aws_client("sts").get_caller_identity()["Arn"]
    except Exception as exc:
        identity = f"erreur : {type(exc).__name__} : {exc}"
    return jsonify(
        pod=socket.gethostname(),
        aws_identity=identity,
        pod_identity_active=bool(os.environ.get("AWS_CONTAINER_CREDENTIALS_FULL_URI")),
        region=AWS_REGION,
        bucket=BUCKET_NAME,
        db_host=DB_HOST,
        db_name=DB_NAME,
        db_secret_arn=DB_SECRET_ARN or None,
    )


@app.errorhandler(413)
def too_large(_):
    flash(f"Fichier trop volumineux (max {MAX_UPLOAD_MB} Mo).", "error")
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
