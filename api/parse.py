import io
import cgi
from http.server import BaseHTTPRequestHandler

import pandas as pd
from parser.parser import process_pdf_bytes


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        # Apenas /api/parse
        # (Em Vercel, esse arquivo já “vira” a rota, mas deixo robusto.)
        try:
            content_type = self.headers.get("content-type", "")
            if "multipart/form-data" not in content_type:
                self._send_text(400, "Envie multipart/form-data com campo 'file'.")
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

            file_item = form["file"]
            pdf_bytes = file_item.file.read()

            df = process_pdf_bytes(pdf_bytes)

            if df is None or df.empty:
                self._send_text(200, "O arquivo enviado não é um relatório de pesquisa de preços do ComprasGov.")
                return

            out = io.BytesIO()
            df.to_excel(out, index=False)
            out.seek(0)
            xlsx_bytes = out.read()

            filename = "relatorio_precos_compoe_comprasgov.xlsx"
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(len(xlsx_bytes)))
            self.end_headers()
            self.wfile.write(xlsx_bytes)

        except Exception as e:
            # Retorno amigável (não traceback)
            self._send_text(500, f"Erro ao processar: {str(e)}")

    def do_GET(self):
        self._send_text(405, "Use POST com multipart/form-data (campo 'file').")

    def _send_text(self, status: int, msg: str):
        data = (msg or "").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
