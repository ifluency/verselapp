import os
import json
import io
import zipfile
import traceback
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import psycopg2
import psycopg2.extras

import boto3
from botocore.config import Config


def _send_json(h: BaseHTTPRequestHandler, status: int, payload: dict):
    data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    h.send_response(status)
    h.send_header("Content-Type", "application/json; charset=utf-8")
    h.send_header("Content-Length", str(len(data)))
    h.end_headers()
    h.wfile.write(data)


def _send_bytes(h: BaseHTTPRequestHandler, status: int, content: bytes, content_type: str, filename: str | None = None):
    h.send_response(status)
    h.send_header("Content-Type", content_type)
    h.send_header("Content-Length", str(len(content)))
    if filename:
        h.send_header("Content-Disposition", f'inline; filename="{filename}"')
    h.end_headers()
    h.wfile.write(content)


def _db_conn():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        return None
    return psycopg2.connect(dsn, sslmode="require")


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


def _ensure_schema(cur):
    # canonical tables
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

    # legacy columns that exist in some DBs (we add if missing so SQL can reference safely)
    cur.execute("ALTER TABLE lista_runs ADD COLUMN IF NOT EXISTS run_number TEXT;")
    cur.execute("ALTER TABLE lista_runs ADD COLUMN IF NOT EXISTS saved_at TIMESTAMPTZ;")
    cur.execute("ALTER TABLE lista_runs ADD COLUMN IF NOT EXISTS presigned_get_url TEXT;")
    cur.execute("ALTER TABLE lista_runs ADD COLUMN IF NOT EXISTS sha256_zip TEXT;")
    cur.execute("ALTER TABLE lista_runs ADD COLUMN IF NOT EXISTS size_bytes BIGINT;")
    cur.execute("ALTER TABLE lista_runs ADD COLUMN IF NOT EXISTS archive_size_byte BIGINT;")
    cur.execute("ALTER TABLE lista_runs ADD COLUMN IF NOT EXISTS r2_key_archive_zip TEXT;")
    cur.execute("ALTER TABLE lista_runs ADD COLUMN IF NOT EXISTS r2_key TEXT;")

    # ensure canonical columns exist too (if table existed without them)
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

    # backfill canonical columns from legacy where possible
    cur.execute(
        """
        UPDATE lista_runs
        SET r2_key_archive = COALESCE(NULLIF(r2_key_archive,''), NULLIF(r2_key_archive_zip,''), NULLIF(r2_key,''))
        """
    )
    cur.execute(
        """
        UPDATE lista_runs
        SET archive_size_bytes = COALESCE(archive_size_bytes, size_bytes, archive_size_byte)
        """
    )
    cur.execute(
        """
        UPDATE lista_runs
        SET archive_sha256 = COALESCE(NULLIF(archive_sha256,''), NULLIF(sha256_zip,''))
        """
    )

    # best-effort: keep created_at populated from saved_at if present
    cur.execute(
        """
        UPDATE lista_runs
        SET created_at = COALESCE(created_at, saved_at, NOW())
        """
    )

    # ensure indexes
    cur.execute("CREATE INDEX IF NOT EXISTS idx_lista_runs_lista_id_created ON lista_runs (lista_id, created_at DESC);")


def _action_runs(cur, filtro_lista: str):
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
            COALESCE(r.archive_size_bytes, r.size_bytes, r.archive_size_byte, 0) AS tamanho_bytes,
            COALESCE(r.r2_key_archive, r.r2_key_archive_zip, r.r2_key, '') AS r2_key_archive,
            COALESCE(NULLIF(r.r2_key_input_pdf,''), '') AS r2_key_input_pdf
        FROM listas l
        LEFT JOIN LATERAL (
            SELECT
                id,
                run_id,
                created_at,
                archive_size_bytes,
                size_bytes,
                archive_size_byte,
                r2_key_archive,
                r2_key_archive_zip,
                r2_key,
                r2_key_input_pdf
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
    return cur.fetchall() or []


def _r2_bucket():
    b = os.environ.get("R2_BUCKET")
    return (b or "").strip()


def _recover_input_pdf_from_zip(conn, s3, bucket: str, run_row: dict) -> str | None:
    """
    Se r2_key_input_pdf estiver vazio, tenta baixar archive.zip e procurar por 'input.pdf'.
    Se achar, envia para <prefix>/input.pdf, atualiza DB e retorna o key.
    """
    r2_key_archive = (run_row.get("r2_key_archive") or run_row.get("r2_key_archive_zip") or run_row.get("r2_key") or "").strip()
    if not r2_key_archive:
        return None

    # download zip
    bio = io.BytesIO()
    s3.download_fileobj(bucket, r2_key_archive, bio)
    bio.seek(0)

    with zipfile.ZipFile(bio, "r") as zf:
        candidates = [n for n in zf.namelist() if n.replace("\\", "/").lower().endswith("/input.pdf") or n.lower() == "input.pdf"]
        if not candidates:
            return None
        name = candidates[0]
        pdf_bytes = zf.read(name)

    prefix = r2_key_archive.rsplit("/", 1)[0]
    new_key = f"{prefix}/input.pdf"

    s3.put_object(
        Bucket=bucket,
        Key=new_key,
        Body=pdf_bytes,
        ContentType="application/pdf",
    )

    with conn.cursor() as cur:
        cur.execute("UPDATE lista_runs SET r2_key_input_pdf = %s WHERE id = %s", (new_key, run_row["id"]))

    return new_key


def _action_load(conn, run_id_or_id: str):
    s3 = _r2_client()
    if s3 is None:
        return 500, {"error": "R2 env vars não configuradas"}
    bucket = _r2_bucket()
    if not bucket:
        return 500, {"error": "R2_BUCKET não configurada"}

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        _ensure_schema(cur)

        cur.execute(
            """
            SELECT
                r.*,
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
            return 404, {"error": "run_id não encontrado"}

    # resolve canonical keys
    r2_key_archive = (row.get("r2_key_archive") or row.get("r2_key_archive_zip") or row.get("r2_key") or "").strip()
    r2_key_input = (row.get("r2_key_input_pdf") or "").strip()

    # attempt recovery for older runs
    if not r2_key_input and r2_key_archive:
        try:
            recovered = _recover_input_pdf_from_zip(conn, s3, bucket, row)
            if recovered:
                r2_key_input = recovered
        except Exception:
            # ignore, will return clear error below
            pass

    if not r2_key_input:
        return 409, {
            "error": "Run não possui r2_key_input_pdf",
            "hint": "Este run foi arquivado sem salvar o PDF de entrada (input.pdf) no R2. Re-arquive a lista para habilitar edição automática.",
            "run_id": str(row.get("run_id") or row.get("id")),
        }

    # IMPORTANT: return same-origin URL to avoid CORS
    run_key = str(row.get("run_id") or row.get("id"))
    input_url = f"/api/archive?action=input&run_id={run_key}"

    return 200, {
        "run_id": run_key,
        "numero_lista": row.get("numero_lista"),
        "nome_lista": row.get("nome_lista"),
        "responsavel": row.get("responsavel"),
        "processo_sei": row.get("processo_sei"),
        "salvo_em": str(row.get("salvo_em")),
        "ultima_edicao_em": str(row.get("ultima_edicao_em")),
        "created_at_run": str(row.get("created_at")),
        "r2_key_archive": r2_key_archive,
        "r2_key_input_pdf": r2_key_input,
        # backwards compatibility with the frontend that expects "input_presigned_url"
        "input_presigned_url": input_url,
        "input_url": input_url,
        "payload_json": row.get("payload_json") or {},
    }


def _action_presign_archive(conn, run_id_or_id: str):
    s3 = _r2_client()
    if s3 is None:
        return 500, {"error": "R2 env vars não configuradas"}
    bucket = _r2_bucket()
    if not bucket:
        return 500, {"error": "R2_BUCKET não configurada"}

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        _ensure_schema(cur)
        cur.execute(
            """
            SELECT COALESCE(NULLIF(r2_key_archive,''), NULLIF(r2_key_archive_zip,''), NULLIF(r2_key,'')) AS k
            FROM lista_runs
            WHERE (run_id::text = %s) OR (id::text = %s)
            ORDER BY created_at DESC NULLS LAST, id DESC
            LIMIT 1
            """,
            (run_id_or_id, run_id_or_id),
        )
        row = cur.fetchone()
        if not row:
            return 404, {"error": "run_id não encontrado"}
        key = (row.get("k") or "").strip()
        if not key:
            return 409, {"error": "Este run não possui archive.zip no R2."}

    url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=int(os.environ.get("R2_PRESIGN_EXPIRES", "3600")),
    )
    return 200, {"url": url}


def _action_input_pdf(conn, run_id_or_id: str):
    s3 = _r2_client()
    if s3 is None:
        return 500, {"error": "R2 env vars não configuradas"}, None
    bucket = _r2_bucket()
    if not bucket:
        return 500, {"error": "R2_BUCKET não configurada"}, None

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        _ensure_schema(cur)
        cur.execute(
            """
            SELECT COALESCE(NULLIF(r2_key_input_pdf,''), '') AS k
            FROM lista_runs
            WHERE (run_id::text = %s) OR (id::text = %s)
            ORDER BY created_at DESC NULLS LAST, id DESC
            LIMIT 1
            """,
            (run_id_or_id, run_id_or_id),
        )
        row = cur.fetchone()
        if not row:
            return 404, {"error": "run_id não encontrado"}, None
        key = (row.get("k") or "").strip()
        if not key:
            return 409, {"error": "Run não possui r2_key_input_pdf. Re-arquive para habilitar edição automática."}, None

    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        body = obj["Body"].read()
        return 200, None, body
    except Exception as e:
        return 500, {"error": f"Falha ao buscar input.pdf no R2: {str(e)}"}, None


def _action_delete(conn, run_id_or_id: str):
    bucket = _r2_bucket()
    s3 = _r2_client()

    deleted_objects = []
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        _ensure_schema(cur)

        cur.execute(
            """
            SELECT id, lista_id,
                   COALESCE(run_id::text, id::text) AS rid,
                   COALESCE(NULLIF(r2_key_archive,''), NULLIF(r2_key_archive_zip,''), NULLIF(r2_key,'')) AS kzip,
                   COALESCE(NULLIF(r2_key_input_pdf,''), '') AS kpdf
            FROM lista_runs
            WHERE (run_id::text = %s) OR (id::text = %s)
            ORDER BY created_at DESC NULLS LAST, id DESC
            LIMIT 1
            """,
            (run_id_or_id, run_id_or_id),
        )
        row = cur.fetchone()
        if not row:
            return 404, {"error": "run_id não encontrado"}

        # delete from R2 (best-effort)
        if s3 and bucket:
            for k in [row.get("kzip"), row.get("kpdf")]:
                k = (k or "").strip()
                if k:
                    try:
                        s3.delete_object(Bucket=bucket, Key=k)
                        deleted_objects.append(k)
                    except Exception:
                        pass

        cur.execute("DELETE FROM lista_runs WHERE id = %s", (row["id"],))

        cur.execute("SELECT COUNT(*) AS c FROM lista_runs WHERE lista_id = %s", (row["lista_id"],))
        c = int((cur.fetchone() or {}).get("c") or 0)
        if c == 0:
            cur.execute("DELETE FROM listas WHERE id = %s", (row["lista_id"],))

    return 200, {"ok": True, "deleted_objects": deleted_objects}


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            q = parse_qs(urlparse(self.path).query)
            action = (q.get("action", [""])[0] or "").strip().lower()

            conn = _db_conn()
            if conn is None:
                return _send_json(self, 500, {"error": "DATABASE_URL não configurada"})

            if action == "runs":
                filtro_lista = (q.get("lista", [""])[0] or "").strip()
                with conn:
                    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                        _ensure_schema(cur)
                        rows = _action_runs(cur, filtro_lista)
                return _send_json(self, 200, {"items": rows})

            if action == "presign":
                run_id = (q.get("run_id", [""])[0] or "").strip()
                if not run_id:
                    return _send_json(self, 400, {"error": "run_id é obrigatório"})
                with conn:
                    status, payload = _action_presign_archive(conn, run_id)
                return _send_json(self, status, payload)

            if action == "load":
                run_id = (q.get("run_id", [""])[0] or "").strip()
                if not run_id:
                    return _send_json(self, 400, {"error": "run_id é obrigatório"})
                with conn:
                    status, payload = _action_load(conn, run_id)
                return _send_json(self, status, payload)

            if action == "input":
                run_id = (q.get("run_id", [""])[0] or "").strip()
                if not run_id:
                    return _send_json(self, 400, {"error": "run_id é obrigatório"})
                with conn:
                    status, payload, body = _action_input_pdf(conn, run_id)
                if status == 200 and body is not None:
                    return _send_bytes(self, 200, body, "application/pdf", "input.pdf")
                return _send_json(self, status, payload or {"error": "Falha ao obter input.pdf"})

            return _send_json(self, 400, {"error": "action inválida. Use: runs | presign | load | input"})

        except Exception as e:
            return _send_json(self, 500, {"error": str(e), "trace": traceback.format_exc()})

    def do_POST(self):
        try:
            q = parse_qs(urlparse(self.path).query)
            action = (q.get("action", [""])[0] or "").strip().lower()

            if action != "delete":
                return _send_json(self, 400, {"error": "action inválida para POST. Use: delete"})

            run_id = (q.get("run_id", [""])[0] or "").strip()
            if not run_id:
                return _send_json(self, 400, {"error": "run_id é obrigatório"})

            conn = _db_conn()
            if conn is None:
                return _send_json(self, 500, {"error": "DATABASE_URL não configurada"})

            with conn:
                status, payload = _action_delete(conn, run_id)
            return _send_json(self, status, payload)

        except Exception as e:
            return _send_json(self, 500, {"error": str(e), "trace": traceback.format_exc()})
