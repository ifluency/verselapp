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


def _column_exists(cur, table: str, column: str) -> bool:
    cur.execute(
        """
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema='public' AND table_name=%s AND column_name=%s
        LIMIT 1
        """,
        (table, column),
    )
    return cur.fetchone() is not None


def _ensure_schema(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS listas (
            id BIGSERIAL PRIMARY KEY,
            numero_lista TEXT UNIQUE NOT NULL,
            nome_lista TEXT,
            responsavel TEXT,
            processo_sei TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS lista_runs (
            id BIGSERIAL PRIMARY KEY,
            lista_id BIGINT REFERENCES listas(id) ON DELETE CASCADE,
            run_id UUID,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            r2_key_archive TEXT,
            r2_key_input_pdf TEXT,
            archive_size_bytes BIGINT,
            archive_sha256 TEXT,
            payload_json JSONB
        );
        """
    )

    cur.execute("ALTER TABLE lista_runs ADD COLUMN IF NOT EXISTS run_id UUID;")
    cur.execute("ALTER TABLE lista_runs ADD COLUMN IF NOT EXISTS r2_key_archive TEXT;")
    cur.execute("ALTER TABLE lista_runs ADD COLUMN IF NOT EXISTS r2_key_input_pdf TEXT;")
    cur.execute("ALTER TABLE lista_runs ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();")

    if _column_exists(cur, "lista_runs", "r2_key") and _column_exists(cur, "lista_runs", "r2_key_archive"):
        cur.execute("UPDATE lista_runs SET r2_key_archive = COALESCE(r2_key_archive, r2_key);")

    try:
        cur.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")
        cur.execute("UPDATE lista_runs SET run_id = gen_random_uuid() WHERE run_id IS NULL;")
    except Exception:
        pass


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            q = parse_qs(urlparse(self.path).query)
            run_id_or_id = (q.get("run_id", [""])[0] or "").strip()
            if not run_id_or_id:
                return _send_json(self, 400, {"error": "run_id é obrigatório"})

            conn = _db_conn()
            if conn is None:
                return _send_json(self, 500, {"error": "DATABASE_URL não configurada"})

            bucket = os.environ.get("R2_BUCKET") or ""
            s3 = _r2_client()

            deleted_objects = []
            with conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    _ensure_schema(cur)

                    cur.execute(
                        """
                        SELECT id, lista_id, COALESCE(run_id::text, id::text) AS rid, r2_key_archive, r2_key_input_pdf
                        FROM lista_runs
                        WHERE (run_id::text = %s) OR (id::text = %s)
                        ORDER BY created_at DESC NULLS LAST, id DESC
                        LIMIT 1
                        """,
                        (run_id_or_id, run_id_or_id),
                    )
                    row = cur.fetchone()
                    if not row:
                        return _send_json(self, 404, {"error": "run_id não encontrado"})

                    # Delete objects from R2 (best-effort)
                    if s3 and bucket:
                        for k in [row.get("r2_key_archive"), row.get("r2_key_input_pdf")]:
                            k = (k or "").strip()
                            if k:
                                try:
                                    s3.delete_object(Bucket=bucket, Key=k)
                                    deleted_objects.append(k)
                                except Exception:
                                    pass

                    # Delete DB row
                    cur.execute("DELETE FROM lista_runs WHERE id = %s", (row["id"],))

                    # If list has no more runs, delete the list row too
                    cur.execute("SELECT COUNT(*) AS c FROM lista_runs WHERE lista_id = %s", (row["lista_id"],))
                    c = int((cur.fetchone() or {}).get("c") or 0)
                    if c == 0:
                        cur.execute("DELETE FROM listas WHERE id = %s", (row["lista_id"],))

            return _send_json(self, 200, {"ok": True, "deleted_objects": deleted_objects})

        except Exception as e:
            return _send_json(self, 500, {"error": str(e), "trace": traceback.format_exc()})
