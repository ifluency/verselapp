import os
import json
import io
import zipfile
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

    # Patch legacy
    cur.execute("ALTER TABLE lista_runs ADD COLUMN IF NOT EXISTS run_id UUID;")
    cur.execute("ALTER TABLE lista_runs ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();")
    cur.execute("ALTER TABLE lista_runs ADD COLUMN IF NOT EXISTS r2_key_archive TEXT;")
    cur.execute("ALTER TABLE lista_runs ADD COLUMN IF NOT EXISTS r2_key_input_pdf TEXT;")
    cur.execute("ALTER TABLE lista_runs ADD COLUMN IF NOT EXISTS payload_json JSONB;")
    cur.execute("ALTER TABLE listas ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();")
    cur.execute("ALTER TABLE listas ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();")

    if _column_exists(cur, "lista_runs", "r2_key") and _column_exists(cur, "lista_runs", "r2_key_archive"):
        cur.execute("UPDATE lista_runs SET r2_key_archive = COALESCE(r2_key_archive, r2_key);")

    try:
        cur.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")
        cur.execute("UPDATE lista_runs SET run_id = gen_random_uuid() WHERE run_id IS NULL;")
    except Exception:
        try:
            cur.execute("CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\";")
            cur.execute("UPDATE lista_runs SET run_id = uuid_generate_v4() WHERE run_id IS NULL;")
        except Exception:
            pass

    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_lista_runs_run_id ON lista_runs (run_id) WHERE run_id IS NOT NULL;")


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            q = parse_qs(urlparse(self.path).query)
            run_id_or_id = (q.get("run_id", [""])[0] or "").strip()
            if not run_id_or_id:
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
                        """
                        SELECT
                            r.id,
                            r.run_id,
                            r.created_at,
                            r.r2_key_archive,
                            r.r2_key_input_pdf,
                            r.payload_json,
                            l.numero_lista,
                            l.nome_lista,
                            l.responsavel,
                            l.processo_sei,
                            l.created_at AS salvo_em,
                            l.updated_at AS ultima_edicao_em
                        FROM lista_runs r
                        JOIN listas l ON l.id = r.lista_id
                        WHERE (r.run_id::text = %s) OR (r.id::text = %s)
                        ORDER BY r.created_at DESC NULLS LAST, r.id DESC
                        LIMIT 1
                        """,
                        (run_id_or_id, run_id_or_id),
                    )
                    row = cur.fetchone()
                    if not row:
                        return _send_json(self, 404, {"error": "run_id não encontrado"})

                    r2_key_archive = (row.get("r2_key_archive") or "").strip()
                    r2_key_input = (row.get("r2_key_input_pdf") or "").strip()

                    if not r2_key_input:
                        if not r2_key_archive:
                            return _send_json(self, 500, {"error": "Run não possui r2_key_archive nem r2_key_input_pdf"})

                        bio = io.BytesIO()
                        s3.download_fileobj(bucket, r2_key_archive, bio)
                        bio.seek(0)

                        with zipfile.ZipFile(bio, "r") as zf:
                            candidates = [n for n in zf.namelist() if n.lower().endswith("input.pdf")]
                            if not candidates:
                                pdfs = [n for n in zf.namelist() if n.lower().endswith(".pdf")]
                                if not pdfs:
                                    return _send_json(self, 500, {"error": "archive.zip não contém PDFs"})
                                candidates = [pdfs[0]]
                            input_name = candidates[0]
                            input_bytes = zf.read(input_name)

                        base_prefix = r2_key_archive.rsplit("/", 1)[0]
                        r2_key_input = f"{base_prefix}/input.pdf"

                        s3.put_object(
                            Bucket=bucket,
                            Key=r2_key_input,
                            Body=input_bytes,
                            ContentType="application/pdf",
                        )

                        cur.execute(
                            "UPDATE lista_runs SET r2_key_input_pdf = %s WHERE id = %s",
                            (r2_key_input, row.get("id")),
                        )

                    input_url = s3.generate_presigned_url(
                        "get_object",
                        Params={"Bucket": bucket, "Key": r2_key_input},
                        ExpiresIn=int(os.environ.get("R2_PRESIGN_EXPIRES", "3600")),
                    )

                    return _send_json(self, 200, {
                        "run_id": str(row.get("run_id") or row.get("id")),
                        "numero_lista": row.get("numero_lista"),
                        "nome_lista": row.get("nome_lista"),
                        "responsavel": row.get("responsavel"),
                        "processo_sei": row.get("processo_sei"),
                        "salvo_em": str(row.get("salvo_em")),
                        "ultima_edicao_em": str(row.get("ultima_edicao_em")),
                        "created_at_run": str(row.get("created_at")),
                        "input_presigned_url": input_url,
                        "payload_json": row.get("payload_json") or {},
                    })

        except Exception as e:
            return _send_json(self, 500, {"error": str(e), "trace": traceback.format_exc()})
