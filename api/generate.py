import io
import cgi
import json
import re
import os
import uuid
import hashlib
import traceback
import zipfile
from datetime import datetime
from urllib.parse import urlparse, parse_qs
from http.server import BaseHTTPRequestHandler


from parser.parser import (
    process_pdf_bytes,
    build_itens_relatorio,
    build_memoria_calculo_pdf_bytes,
    build_pdf_tabela_comparativa_bytes,
    PdfIncompatibilityError,
)


def _safe_slug(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"[^0-9A-Za-z._-]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "SEM_NUMERO"


def _safe_filename(s: str) -> str:
    """Nome amigável para arquivo (mantém espaços), removendo caracteres proibidos."""
    s = (s or "").strip()
    s = re.sub(r"[<>:\"/\\|?*\n\r\t]+", "_", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s or "ARQUIVO"


def _sha256_hex(data: bytes) -> str:
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


def _now_stamp() -> str:
    # Sem dependências externas. Mantém padrão ordenável e adequado para SEI/SharePoint.
    return datetime.now().strftime("%Y-%m-%d %H%M")


def _build_archive_zip(
    *,
    input_pdf_bytes: bytes,
    input_pdf_name: str,
    pdf1_name: str,
    pdf1_bytes: bytes,
    pdf2_name: str,
    pdf2_bytes: bytes,
    manifest: dict,
) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        # Mantém nomes previsíveis dentro do ZIP
        zf.writestr("input.pdf", input_pdf_bytes)
        zf.writestr(pdf1_name, pdf1_bytes)
        zf.writestr(pdf2_name, pdf2_bytes)
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
        # Para auditabilidade humana (não é obrigatório)
        zf.writestr("README.txt", "Pacote de arquivamento gerado pelo sistema.\nContém o PDF de entrada e os PDFs finais.\n")
    out.seek(0)
    return out.read()


def _r2_s3_client():
    """Cria client S3 compatível com Cloudflare R2. Retorna None se não configurado."""
    access_key = (os.environ.get("R2_ACCESS_KEY_ID") or "").strip()
    secret_key = (os.environ.get("R2_SECRET_ACCESS_KEY") or "").strip()
    bucket = (os.environ.get("R2_BUCKET") or "").strip()
    endpoint = (os.environ.get("R2_ENDPOINT") or "").strip()
    account_id = (os.environ.get("R2_ACCOUNT_ID") or "").strip()
    region = (os.environ.get("R2_REGION") or "auto").strip() or "auto"

    if not access_key or not secret_key or not bucket:
        return None
    if not endpoint:
        if not account_id:
            return None
        endpoint = f"https://{account_id}.r2.cloudflarestorage.com"

    try:
        import boto3  # type: ignore
        from botocore.config import Config  # type: ignore

        return (
            boto3.client(
                "s3",
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                region_name=region,
                endpoint_url=endpoint,
                config=Config(signature_version="s3v4"),
            ),
            bucket,
        )
    except Exception as e:
        print("R2: boto3/botocore indisponível:", str(e))
        return None


from typing import Optional, Dict, Any


def _upload_to_r2_and_presign(*, data: bytes, key: str) -> Optional[Dict[str, Any]]:
    cfg = _r2_s3_client()
    if cfg is None:
        return None
    s3, bucket = cfg

    sha = _sha256_hex(data)
    try:
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=data,
            ContentType="application/zip",
            Metadata={"sha256": sha},
        )
    except Exception as e:
        print("R2: falha ao enviar objeto:", str(e))
        return None

    expires = int((os.environ.get("R2_PRESIGN_EXPIRES") or "3600").strip() or "3600")
    signed_url = ""
    try:
        signed_url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expires,
        )
    except Exception as e:
        print("R2: falha ao gerar presigned URL:", str(e))

    return {"bucket": bucket, "key": key, "sha256": sha, "signed_url": signed_url}


def _ensure_and_save_run_in_db(
    *,
    lista_meta: dict,
    payload: dict,
    run_id: str,
    r2_bucket: str,
    r2_key: str,
    sha256_zip: str,
    size_bytes: int,
    archive_filename: str,
    input_original_name: str,
) -> Optional[Dict[str, Any]]:
    dsn = (os.environ.get("DATABASE_URL") or "").strip()
    if not dsn:
        return None

    try:
        import psycopg2  # type: ignore
        from psycopg2.extras import Json  # type: ignore
    except Exception as e:
        print("DB: psycopg2 indisponível:", str(e))
        return None

    numero_lista = str(lista_meta.get("numero_lista") or lista_meta.get("numero") or "").strip()
    if not numero_lista:
        return None

    nome_lista = str(lista_meta.get("nome_lista") or lista_meta.get("nome") or "").strip() or None
    processo_sei = str(lista_meta.get("processo_sei") or lista_meta.get("sei") or "").strip() or None
    responsavel = str(lista_meta.get("responsavel") or "").strip() or None

    conn = psycopg2.connect(dsn, sslmode="require")
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS listas (
                  id BIGSERIAL PRIMARY KEY,
                  numero_lista TEXT UNIQUE NOT NULL,
                  nome_lista TEXT,
                  processo_sei TEXT,
                  responsavel_atual TEXT,
                  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS lista_runs (
                  id UUID PRIMARY KEY,
                  lista_id BIGINT NOT NULL REFERENCES listas(id) ON DELETE CASCADE,
                  run_number INT NOT NULL,
                  saved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                  responsavel TEXT,
                  r2_bucket TEXT,
                  r2_key TEXT,
                  sha256_zip TEXT,
                  size_bytes BIGINT,
                  archive_filename TEXT,
                  input_filename TEXT,
                  payload_json JSONB
                );
                """
            )

            cur.execute(
                """
                INSERT INTO listas (numero_lista, nome_lista, processo_sei, responsavel_atual)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (numero_lista)
                DO UPDATE SET
                  nome_lista = EXCLUDED.nome_lista,
                  processo_sei = EXCLUDED.processo_sei,
                  responsavel_atual = EXCLUDED.responsavel_atual,
                  updated_at = now()
                RETURNING id;
                """,
                (numero_lista, nome_lista, processo_sei, responsavel),
            )
            lista_id = cur.fetchone()[0]

            cur.execute("SELECT COALESCE(MAX(run_number),0) FROM lista_runs WHERE lista_id=%s;", (lista_id,))
            run_number = int(cur.fetchone()[0] or 0) + 1

            cur.execute(
                """
                INSERT INTO lista_runs (
                  id, lista_id, run_number, responsavel, r2_bucket, r2_key,
                  sha256_zip, size_bytes, archive_filename, input_filename, payload_json
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s);
                """,
                (
                    run_id,
                    lista_id,
                    run_number,
                    responsavel,
                    r2_bucket,
                    r2_key,
                    sha256_zip,
                    size_bytes,
                    archive_filename,
                    input_original_name,
                    Json(payload or {}),
                ),
            )

        conn.commit()
        return {"lista_id": lista_id, "run_number": run_number}
    except Exception as e:
        conn.rollback()
        print("DB: falha ao persistir run:", str(e))
        return None
    finally:
        conn.close()


def _notify_power_automate(*, body: dict) -> None:
    url = (os.environ.get("POWER_AUTOMATE_WEBHOOK_URL") or "").strip()
    if not url:
        return
    try:
        import urllib.request

        req = urllib.request.Request(
            url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=25) as resp:
            _ = resp.read()
    except Exception as e:
        print("Power Automate: falha ao acionar flow:", str(e))


def _best_effort_archive_save(
    *,
    input_pdf_bytes: bytes,
    input_pdf_original_name: str,
    pdf1_name: str,
    pdf1_bytes: bytes,
    pdf2_name: str,
    pdf2_bytes: bytes,
    lista_meta: dict,
    payload: dict,
) -> None:
    """Fluxo de arquivamento (R2 + Power Automate + DB). Nunca quebra o download."""

    numero_lista_raw = str(lista_meta.get("numero_lista") or lista_meta.get("numero") or "").strip()
    responsavel_raw = str(lista_meta.get("responsavel") or "").strip()

    if not numero_lista_raw:
        return

    stamp = _now_stamp()
    archive_filename = _safe_filename(f"{numero_lista_raw} - {responsavel_raw} - Pesquisa de Preços - {stamp}.zip")

    run_id = str(uuid.uuid4())
    now = datetime.now()
    year = now.strftime("%Y")
    numero_slug = _safe_slug(numero_lista_raw)

    manifest = {
        "run_id": run_id,
        "saved_at": now.isoformat(),
        "archive_filename": archive_filename,
        "input_original_name": input_pdf_original_name,
        "lista_meta": lista_meta,
        "payload": payload,
        "app_build": os.environ.get("VERCEL_GIT_COMMIT_SHA") or os.environ.get("GIT_COMMIT_SHA") or "",
    }

    archive_zip_bytes = _build_archive_zip(
        input_pdf_bytes=input_pdf_bytes,
        input_pdf_name=input_pdf_original_name,
        pdf1_name=pdf1_name,
        pdf1_bytes=pdf1_bytes,
        pdf2_name=pdf2_name,
        pdf2_bytes=pdf2_bytes,
        manifest=manifest,
    )

    r2_prefix = (os.environ.get("R2_PREFIX") or "precos").strip().strip("/")
    r2_key = f"{r2_prefix}/{year}/{numero_slug}/{run_id}/archive.zip"

    r2_res = _upload_to_r2_and_presign(data=archive_zip_bytes, key=r2_key)
    if not r2_res:
        return

    # DB (opcional)
    db_res = _ensure_and_save_run_in_db(
        lista_meta=lista_meta,
        payload=payload,
        run_id=run_id,
        r2_bucket=r2_res["bucket"],
        r2_key=r2_res["key"],
        sha256_zip=r2_res["sha256"],
        size_bytes=len(archive_zip_bytes),
        archive_filename=archive_filename,
        input_original_name=input_pdf_original_name,
    )

    # Power Automate (opcional)
    if r2_res.get("signed_url"):
        sp_root = (os.environ.get("SHAREPOINT_ROOT_FOLDER") or "Pesquisa de Preços").strip().strip("/")
        folder = f"{sp_root}/{year}/Lista {numero_slug}"

        body = {
            "file_name": archive_filename,
            "sharepoint_folder": folder,
            "download_url": r2_res["signed_url"],
            "r2_bucket": r2_res["bucket"],
            "r2_key": r2_res["key"],
            "sha256": r2_res["sha256"],
            "numero_lista": numero_lista_raw,
            "responsavel": responsavel_raw,
            "run_id": run_id,
            "run_number": (db_res or {}).get("run_number"),
        }
        _notify_power_automate(body=body)


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

            file_field = form["file"]
            input_filename = getattr(file_field, "filename", None) or "input.pdf"
            input_filename = _safe_filename(str(input_filename))
            pdf_bytes = file_field.file.read()

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

            lista_meta = payload.get("lista_meta") or payload.get("lista") or {}
            if not isinstance(lista_meta, dict):
                lista_meta = {}

            pdf_comp_bytes = build_pdf_tabela_comparativa_bytes(itens, meta=lista_meta)

            numero = _safe_slug(str(lista_meta.get("numero_lista") or lista_meta.get("numero") or ""))
            pdf1_name = f"Tabela_Final_de_Precos_{numero}.pdf"
            pdf2_name = f"Relatorio_Comparativo_de_Valores_{numero}.pdf"

            # ZIP para download (mantém comportamento atual)
            zip_out = io.BytesIO()
            with zipfile.ZipFile(zip_out, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(pdf1_name, pdf_comp_bytes)
                zf.writestr(pdf2_name, pdf_memoria_bytes)
            zip_out.seek(0)
            zip_bytes = zip_out.read()

            # Arquivamento (best-effort) — não pode quebrar o download
            try:
                _best_effort_archive_save(
                    input_pdf_bytes=pdf_bytes,
                    input_pdf_original_name=input_filename,
                    pdf1_name=pdf1_name,
                    pdf1_bytes=pdf_comp_bytes,
                    pdf2_name=pdf2_name,
                    pdf2_bytes=pdf_memoria_bytes,
                    lista_meta=lista_meta,
                    payload=payload,
                )
            except Exception as e:
                print("Archive: falha inesperada (ignorada):", str(e))

            filename = f"Formacao_Precos_Referencia_Lista_{numero}.zip"
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
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
