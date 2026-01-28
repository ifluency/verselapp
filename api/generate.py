import io
import cgi
import json
import re
import os
import uuid
import hashlib
import traceback
import zipfile
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs
from http.server import BaseHTTPRequestHandler

# Optional deps (installed via requirements.txt)
try:
    import boto3
    from botocore.config import Config as BotoConfig
except Exception:  # pragma: no cover
    boto3 = None
    BotoConfig = None

try:
    import psycopg2
    from psycopg2.extras import Json as PgJson
except Exception:  # pragma: no cover
    psycopg2 = None
    PgJson = None


def _safe_slug(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"[^0-9A-Za-z._-]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "SEM_NUMERO"


def _utc_now():
    return datetime.now(timezone.utc)


def _sha256_bytes(b: bytes) -> str:
    h = hashlib.sha256()
    h.update(b)
    return h.hexdigest()


def _r2_client_from_env():
    if boto3 is None:
        return None, "boto3 não instalado"
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


def _upload_archive_to_r2(archive_bytes: bytes, lista_meta: dict):
    """
    Best-effort.
    Returns: (run_id, r2_key, presigned_url, err)
    """
    bucket = (os.environ.get("R2_BUCKET") or "").strip()
    prefix = (os.environ.get("R2_PREFIX") or "precos").strip().strip("/")
    expires = int((os.environ.get("R2_PRESIGN_EXPIRES") or "3600").strip() or "3600")

    if not bucket:
        return None, None, None, "R2_BUCKET ausente"

    s3, err = _r2_client_from_env()
    if s3 is None:
        return None, None, None, err or "cliente R2 indisponível"

    run_id = str(uuid.uuid4())

    numero = str(lista_meta.get("numero_lista") or lista_meta.get("numero") or "").strip()
    responsavel = str(lista_meta.get("responsavel") or "").strip()
    ts = _utc_now()

    display_name = f"{numero} - {responsavel} - Pesquisa de Preços - {ts.strftime('%Y-%m-%d %H%M')}.zip".strip()
    numero_slug = _safe_slug(numero)
    year = ts.strftime("%Y")
    r2_key = f"{prefix}/{year}/{numero_slug}/{run_id}/archive.zip"

    try:
        s3.put_object(
            Bucket=bucket,
            Key=r2_key,
            Body=archive_bytes,
            ContentType="application/zip",
            Metadata={
                "display_name": display_name[:2000],
                "numero_lista": numero[:2000],
                "responsavel": responsavel[:2000],
                "run_id": run_id,
            },
        )
        presigned = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": r2_key},
            ExpiresIn=expires,
        )
        return run_id, r2_key, presigned, ""
    except Exception as e:
        return None, None, None, str(e)


def _persist_run_to_neon(run_id: str, r2_key: str, presigned_url: str, archive_sha256: str, archive_size: int, lista_meta: dict, payload: dict):
    """
    Best-effort. Creates tables if not exist.
    """
    dsn = (os.environ.get("DATABASE_URL") or "").strip()
    if not dsn or psycopg2 is None:
        return "DATABASE_URL ausente ou psycopg2 indisponível"

    numero = str(lista_meta.get("numero_lista") or lista_meta.get("numero") or "").strip() or "SEM_NUMERO"
    nome = str(lista_meta.get("nome_lista") or lista_meta.get("nome") or "").strip()
    processo = str(lista_meta.get("processo_sei") or lista_meta.get("processo") or "").strip()
    responsavel = str(lista_meta.get("responsavel") or "").strip()

    try:
        conn = psycopg2.connect(dsn, sslmode="require")
        try:
            conn.autocommit = False
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

                # Upsert lista
                cur.execute(
                    "INSERT INTO listas (numero_lista, nome_lista, processo_sei, responsavel_atual)"
                    " VALUES (%s, %s, %s, %s)"
                    " ON CONFLICT (numero_lista) DO UPDATE"
                    "   SET nome_lista = EXCLUDED.nome_lista,"
                    "       processo_sei = EXCLUDED.processo_sei,"
                    "       responsavel_atual = EXCLUDED.responsavel_atual,"
                    "       updated_at = NOW()"
                    " RETURNING id;",
                    (numero, nome, processo, responsavel),
                )
                lista_id = cur.fetchone()[0]

                # Próximo run_number
                cur.execute("SELECT COALESCE(MAX(run_number), 0) FROM lista_runs WHERE lista_id = %s;", (lista_id,))
                run_number = int(cur.fetchone()[0] or 0) + 1

                cur.execute(
                    "INSERT INTO lista_runs (id, lista_id, run_number, r2_key_archive_zip, presigned_get_url, sha256_zip, size_bytes, payload_json)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s);",
                    (
                        run_id,
                        lista_id,
                        run_number,
                        r2_key,
                        presigned_url,
                        archive_sha256,
                        archive_size,
                        PgJson(payload) if PgJson else json.dumps(payload, ensure_ascii=False),
                    ),
                )

            conn.commit()
            return ""
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    except Exception as e:
        return str(e)


from parser.parser import (
    process_pdf_bytes,
    build_itens_relatorio,
    build_memoria_calculo_pdf_bytes,
    build_pdf_tabela_comparativa_bytes,
    PdfIncompatibilityError,
)


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        q = parse_qs(urlparse(self.path).query)
        debug_mode = q.get("debug", ["0"])[0] in ("1", "true", "True", "yes", "sim")

        try:
            content_type = self.headers.get("content-type", "")
            if "multipart/form-data" not in content_type:
                self._send_text(400, "Envie multipart/form-data com campo 'file' e opcional 'payload'.")
                return

            content_length = int(self.headers.get("content-length", "0"))
            if content_length <= 0:
                self._send_text(400, "Corpo vazio.")
                return

            body = self.rfile.read(content_length)
            environ = {
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": content_type,
                "CONTENT_LENGTH": str(content_length),
            }
            fp = io.BytesIO(body)
            form = cgi.FieldStorage(fp=fp, environ=environ, keep_blank_values=True)

            if "file" not in form:
                self._send_text(400, "Campo 'file' ausente.")
                return

            uploaded_filename = getattr(form["file"], "filename", None) or "input.pdf"
            pdf_bytes = form["file"].file.read()

            payload = {}
            if "payload" in form:
                try:
                    payload_text = form["payload"].value
                    if payload_text:
                        payload = json.loads(payload_text)
                except Exception:
                    payload = {}

            df = process_pdf_bytes(pdf_bytes)
            if df is None or df.empty:
                self._send_text(200, "Nenhuma linha com Compõe=Sim foi encontrada no arquivo enviado.")
                return

            itens = build_itens_relatorio(df, payload=payload)
            pdf_memoria_bytes = build_memoria_calculo_pdf_bytes(df, payload=payload)

            # Meta da lista (título do PDF comparativo)
            lista_meta = payload.get("lista_meta") or payload.get("lista") or {}
            if not isinstance(lista_meta, dict):
                lista_meta = {}

            pdf_comp_bytes = build_pdf_tabela_comparativa_bytes(itens, meta=lista_meta)

            numero = _safe_slug(str(lista_meta.get("numero_lista") or lista_meta.get("numero") or ""))

            # ZIP de download (como antes)
            zip_out = io.BytesIO()
            with zipfile.ZipFile(zip_out, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
                pdf1_name = f"Tabela_Final_de_Precos_{numero}.pdf"
                pdf2_name = f"Relatorio_Comparativo_de_Valores_{numero}.pdf"
                zf.writestr(pdf1_name, pdf_comp_bytes)
                zf.writestr(pdf2_name, pdf_memoria_bytes)

            zip_out.seek(0)
            zip_bytes = zip_out.read()

            # ZIP técnico (arquivamento): input + 2 PDFs + manifest
            archive_out = io.BytesIO()
            ts = _utc_now()
            manifest = {
                "generated_at_utc": ts.isoformat(),
                "input_original_filename": uploaded_filename,
                "lista_meta": lista_meta,
                "zip_download_filename": f"Formacao_Precos_Referencia_Lista_{numero}.zip",
                "files": [
                    {"name": "input.pdf", "type": "application/pdf"},
                    {"name": f"Tabela_Final_de_Precos_{numero}.pdf", "type": "application/pdf"},
                    {"name": f"Relatorio_Comparativo_de_Valores_{numero}.pdf", "type": "application/pdf"},
                    {"name": "manifest.json", "type": "application/json"},
                ],
            }

            with zipfile.ZipFile(archive_out, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("input.pdf", pdf_bytes)
                zf.writestr(f"Tabela_Final_de_Precos_{numero}.pdf", pdf_comp_bytes)
                zf.writestr(f"Relatorio_Comparativo_de_Valores_{numero}.pdf", pdf_memoria_bytes)
                zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))

            archive_out.seek(0)
            archive_bytes = archive_out.read()
            archive_sha = _sha256_bytes(archive_bytes)
            archive_size = len(archive_bytes)

            # Best-effort: R2 + Neon (não quebra o download)
            run_id = None
            presigned_url = None
            r2_key = None
            archive_err = ""

            if os.environ.get("R2_ACCESS_KEY_ID") and os.environ.get("R2_SECRET_ACCESS_KEY") and os.environ.get("R2_BUCKET") and os.environ.get("R2_ENDPOINT"):
                run_id, r2_key, presigned_url, archive_err = _upload_archive_to_r2(archive_bytes, lista_meta)

                if run_id and r2_key:
                    db_err = _persist_run_to_neon(
                        run_id=run_id,
                        r2_key=r2_key,
                        presigned_url=presigned_url or "",
                        archive_sha256=archive_sha,
                        archive_size=archive_size,
                        lista_meta=lista_meta,
                        payload=payload,
                    )
                    if db_err:
                        print("WARN persist Neon:", db_err)

            # Resposta: ZIP para download + headers com auditoria
            filename = f"Formacao_Precos_Referencia_Lista_{numero}.zip"
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')

            if run_id:
                self.send_header("X-Archive-Run-Id", run_id)
            if r2_key:
                self.send_header("X-Archive-R2-Key", r2_key)
            if presigned_url:
                self.send_header("X-Archive-Url", presigned_url)
            if archive_err and debug_mode:
                self.send_header("X-Archive-Warn", _safe_slug(archive_err)[:180])

            self.send_header("Content-Length", str(len(zip_bytes)))
            self.end_headers()
            self.wfile.write(zip_bytes)

        except PdfIncompatibilityError as e:
            self._send_text(400, str(e))

        except Exception as e:
            tb = traceback.format_exc()
            print("ERROR /api/generate:", str(e))
            print(tb)

            if debug_mode:
                self._send_text(500, f"Erro ao processar:\n{str(e)}\n\nSTACKTRACE:\n{tb}")
            else:
                self._send_text(500, "Falha ao processar. Tente novamente ou use /api/generate?debug=1 para ver detalhes.")

    def do_GET(self):
        self._send_text(405, "Use POST com multipart/form-data (campo 'file' e opcional 'payload').")

    def _send_text(self, status: int, msg: str):
        data = (msg or "").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
