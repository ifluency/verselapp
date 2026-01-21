import io
import cgi
import json
import re
import traceback
import zipfile
from urllib.parse import urlparse, parse_qs
from http.server import BaseHTTPRequestHandler



def _safe_slug(s: str) -> str:
    s = (s or '').strip()
    # Mantém caracteres seguros para nomes de arquivo
    s = re.sub(r'[^0-9A-Za-z._-]+', '_', s)
    s = re.sub(r'_+', '_', s).strip('_')
    return s or 'SEM_NUMERO'

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

            zip_out = io.BytesIO()
            with zipfile.ZipFile(zip_out, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
                numero = _safe_slug(str(lista_meta.get("numero_lista") or lista_meta.get("numero") or ""))
                pdf1_name = f"Tabela_Final_de_Precos_{numero}.pdf"
                pdf2_name = f"Relatorio_Comparativo_de_Valores_{numero}.pdf"
                zf.writestr(pdf1_name, pdf_comp_bytes)
                zf.writestr(pdf2_name, pdf_memoria_bytes)

            zip_out.seek(0)
            zip_bytes = zip_out.read()

            numero = _safe_slug(str(lista_meta.get("numero_lista") or lista_meta.get("numero") or ""))
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
