import os
import json
import traceback
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import psycopg2
import psycopg2.extras


def _send_json(h: BaseHTTPRequestHandler, status: int, payload: dict):
    data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    h.send_response(status)
    h.send_header("Content-Type", "application/json; charset=utf-8")
    h.send_header("Content-Length", str(len(data)))
    h.end_headers()
    h.wfile.write(data)


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

    # Patch legacy schemas
    cur.execute("ALTER TABLE listas ADD COLUMN IF NOT EXISTS nome_lista TEXT;")
    cur.execute("ALTER TABLE listas ADD COLUMN IF NOT EXISTS responsavel TEXT;")
    cur.execute("ALTER TABLE listas ADD COLUMN IF NOT EXISTS processo_sei TEXT;")
    cur.execute("ALTER TABLE listas ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();")
    cur.execute("ALTER TABLE listas ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();")

    cur.execute("ALTER TABLE lista_runs ADD COLUMN IF NOT EXISTS run_id UUID;")
    cur.execute("ALTER TABLE lista_runs ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();")
    cur.execute("ALTER TABLE lista_runs ADD COLUMN IF NOT EXISTS r2_key_archive TEXT;")
    cur.execute("ALTER TABLE lista_runs ADD COLUMN IF NOT EXISTS r2_key_input_pdf TEXT;")
    cur.execute("ALTER TABLE lista_runs ADD COLUMN IF NOT EXISTS archive_size_bytes BIGINT;")
    cur.execute("ALTER TABLE lista_runs ADD COLUMN IF NOT EXISTS archive_sha256 TEXT;")
    cur.execute("ALTER TABLE lista_runs ADD COLUMN IF NOT EXISTS payload_json JSONB;")

    # Copy forward legacy column names if present
    if _column_exists(cur, "lista_runs", "r2_key") and _column_exists(cur, "lista_runs", "r2_key_archive"):
        cur.execute("UPDATE lista_runs SET r2_key_archive = COALESCE(r2_key_archive, r2_key);")
    if _column_exists(cur, "lista_runs", "archive_size_byte") and _column_exists(cur, "lista_runs", "archive_size_bytes"):
        cur.execute("UPDATE lista_runs SET archive_size_bytes = COALESCE(archive_size_bytes, archive_size_byte);")

    # Backfill run_id if possible
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
    cur.execute("CREATE INDEX IF NOT EXISTS idx_lista_runs_lista_id_created ON lista_runs (lista_id, created_at DESC);")


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            q = parse_qs(urlparse(self.path).query)
            filtro_lista = (q.get("lista", [""])[0] or "").strip()

            conn = _db_conn()
            if conn is None:
                return _send_json(self, 500, {"error": "DATABASE_URL não configurada"})

            with conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    _ensure_schema(cur)

                    params = []
                    where = ""
                    if filtro_lista:
                        where = "WHERE l.numero_lista ILIKE %s"
                        params.append(f"%{filtro_lista}%")

                    cur.execute(
                        f"""
                        SELECT
                            l.numero_lista,
                            l.nome_lista,
                            l.responsavel,
                            l.processo_sei,
                            l.created_at AS salvo_em,
                            l.updated_at AS ultima_edicao_em,
                            COALESCE(r.run_id::text, r.id::text) AS latest_run_id,
                            COALESCE(r.archive_size_bytes, 0) AS tamanho_bytes,
                            COALESCE(r.r2_key_archive, '') AS r2_key_archive,
                            COALESCE(r.r2_key_input_pdf, '') AS r2_key_input_pdf
                        FROM listas l
                        LEFT JOIN LATERAL (
                            SELECT id, run_id, archive_size_bytes, r2_key_archive, r2_key_input_pdf
                            FROM lista_runs
                            WHERE lista_id = l.id
                            ORDER BY created_at DESC NULLS LAST, id DESC
                            LIMIT 1
                        ) r ON TRUE
                        {where}
                        ORDER BY l.updated_at DESC NULLS LAST, l.id DESC
                        LIMIT 500
                        """,
                        tuple(params),
                    )
                    rows = cur.fetchall() or []

            return _send_json(self, 200, {"items": rows})

        except Exception as e:
            return _send_json(self, 500, {"error": str(e), "trace": traceback.format_exc()})
