import os
import json
import traceback
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import boto3
from botocore.config import Config
import psycopg2
import psycopg2.extras


def _send_json(h: BaseHTTPRequestHandler, status: int, payload: dict):
    data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    h.send_response(status)
    h.send_header("Content-Type", "application/json; charset=utf-8")
    h.send_header("Content-Length", str(len(data)))
    h.end_headers()
    h.wfile.write(data)


def _r2_client():
    endpoint = os.environ.get("R2_ENDPOINT")
    access_key = os.environ.get("R2_ACCESS_KEY_ID")
    secret_key = os.environ.get("R2_SECRET_ACCESS_KEY")
    region = os.environ.get("R2_REGION", "auto")
    if not endpoint or not access_key or not secret_key:
        return None
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
        config=Config(signature_version="s3v4"),
    )


def _db_conn():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        return None
    return psycopg2.connect(dsn, sslmode="require")


def _ensure_schema(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS listas (
            id BIGSERIAL PRIMARY KEY,
            numero_lista TEXT UNIQUE NOT NULL,
            nome_lista TEXT,
            responsavel TEXT,
            processo_sei TEXT
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS lista_runs (
            id BIGSERIAL PRIMARY KEY,
            lista_id BIGINT REFERENCES listas(id) ON DELETE CASCADE,
            run_id UUID UNIQUE NOT NULL,
            r2_key_archive TEXT,
            r2_key_input_pdf TEXT,
            archive_size_bytes BIGINT,
            archive_sha256 TEXT,
            payload_json JSONB
        );
        """
    )
    cur.execute("ALTER TABLE listas ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();")
    cur.execute("ALTER TABLE listas ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();")
    cur.execute("ALTER TABLE lista_runs ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_lista_runs_lista_id_created ON lista_runs (lista_id, created_at DESC);")


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            q = parse_qs(urlparse(self.path).query)
            run_id = (q.get("run_id", [""])[0] or "").strip()
            if not run_id:
                return _send_json(self, 400, {"error": "run_id é obrigatório"})

            conn = _db_conn()
            if conn is None:
                return _send_json(self, 500, {"error": "DATABASE_URL não configurada"})

            s3 = _r2_client()
            if s3 is None:
                return _send_json(self, 500, {"error": "R2 env vars não configuradas"})

            bucket = os.environ.get("R2_BUCKET")
            if not bucket:
                return _send_json(self, 500, {"error": "R2_BUCKET não configurada"})

            with conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    _ensure_schema(cur)
                    cur.execute(
                        "SELECT r2_key_archive FROM lista_runs WHERE run_id = %s LIMIT 1",
                        (run_id,),
                    )
                    row = cur.fetchone()
                    if not row:
                        return _send_json(self, 404, {"error": "run_id não encontrado"})

                    key = (row.get("r2_key_archive") or "").strip()
                    if not key:
                        return _send_json(self, 500, {"error": "Run não possui r2_key_archive"})

            url = s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": bucket, "Key": key},
                ExpiresIn=int(os.environ.get("R2_PRESIGN_EXPIRES", "3600")),
            )
            return _send_json(self, 200, {"url": url})

        except Exception as e:
            return _send_json(self, 500, {"error": str(e), "trace": traceback.format_exc()})
