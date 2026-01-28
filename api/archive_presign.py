import json
import os
import re
import traceback
from urllib.parse import urlparse, parse_qs
from http.server import BaseHTTPRequestHandler

try:
    import psycopg2
except Exception:  # pragma: no cover
    psycopg2 = None

try:
    import boto3
    from botocore.config import Config as BotoConfig
except Exception:  # pragma: no cover
    boto3 = None
    BotoConfig = None


_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _send_json(h: BaseHTTPRequestHandler, status: int, payload: dict):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    h.send_response(status)
    h.send_header("Content-Type", "application/json; charset=utf-8")
    h.send_header("Content-Length", str(len(data)))
    h.end_headers()
    h.wfile.write(data)


def _r2_client_from_env():
    if boto3 is None:
        return None, "boto3 indisponível"
    access_key = (os.environ.get("R2_ACCESS_KEY_ID") or "").strip()
    secret_key = (os.environ.get("R2_SECRET_ACCESS_KEY") or "").strip()
    endpoint = (os.environ.get("R2_ENDPOINT") or "").strip()
    region = (os.environ.get("R2_REGION") or "auto").strip() or "auto"
    if not access_key or not secret_key or not endpoint:
        return None, "env R2 incompleta"
    try:
        s3 = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            config=BotoConfig(signature_version="s3v4") if BotoConfig else None,
        )
        return s3, ""
    except Exception as e:
        return None, str(e)



def _ensure_schema(conn):
    """Garante que as tabelas mínimas existam (evita UndefinedTable em ambientes novos)."""
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS listas ("
            " id SERIAL PRIMARY KEY,"
            " numero_lista TEXT UNIQUE NOT NULL,"
            " nome_lista TEXT,"
            " processo_sei TEXT,"
            " responsavel_atual TEXT,"
            " created_at TIMESTAMPTZ DEFAULT NOW(),"
            " updated_at TIMESTAMPTZ DEFAULT NOW()"
            ");"
        )
        cur.execute(
            "CREATE TABLE IF NOT EXISTS lista_runs ("
            " id UUID PRIMARY KEY,"
            " lista_id INTEGER NOT NULL REFERENCES listas(id) ON DELETE CASCADE,"
            " run_number INTEGER NOT NULL,"
            " saved_at TIMESTAMPTZ DEFAULT NOW(),"
            " r2_key_archive_zip TEXT NOT NULL,"
            " presigned_get_url TEXT,"
            " sha256_zip TEXT,"
            " size_bytes BIGINT,"
            " payload_json JSONB"
            ");"
        )
    conn.commit()

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            if psycopg2 is None:
                return _send_json(self, 500, {"error": "psycopg2 indisponível"})

            dsn = (os.environ.get("DATABASE_URL") or "").strip()
            if not dsn:
                return _send_json(self, 400, {"error": "DATABASE_URL não configurada"})

            bucket = (os.environ.get("R2_BUCKET") or "").strip()
            if not bucket:
                return _send_json(self, 400, {"error": "R2_BUCKET não configurada"})

            q = parse_qs(urlparse(self.path).query)
            run_id = (q.get("run_id", [""])[0] or "").strip()
            if not run_id:
                return _send_json(self, 400, {"error": "Parâmetro 'run_id' é obrigatório"})
            if not _UUID_RE.match(run_id):
                return _send_json(self, 400, {"error": "run_id inválido"})

            expires = int((os.environ.get("R2_PRESIGN_EXPIRES") or "3600").strip() or "3600")
            expires = max(60, min(expires, 60 * 60 * 24))  # 1 min .. 24h

            conn = psycopg2.connect(dsn, sslmode="require")

            _ensure_schema(conn)
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT r.r2_key_archive_zip FROM lista_runs r WHERE r.id = %s::uuid;",
                        (run_id,),
                    )
                    row = cur.fetchone()
                    if not row:
                        return _send_json(self, 404, {"error": "Run não encontrado"})
                    r2_key = row[0]
            finally:
                conn.close()

            s3, err = _r2_client_from_env()
            if s3 is None:
                return _send_json(self, 500, {"error": err or "cliente R2 indisponível"})

            url = s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": bucket, "Key": r2_key},
                ExpiresIn=expires,
            )

            return _send_json(self, 200, {"run_id": run_id, "r2_key": r2_key, "expires_in": expires, "url": url})

        except Exception as e:
            return _send_json(self, 500, {"error": str(e), "trace": traceback.format_exc()})
