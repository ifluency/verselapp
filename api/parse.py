# api/parse.py
# -*- coding: utf-8 -*-

import io
import cgi
import zipfile
from http.server import BaseHTTPRequestHandler

from parser import process_pdf_bytes, build_memoria_calculo_pdf


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
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

            pdf_bytes = form["file"].file.read()

            df = process_pdf_bytes(pdf_bytes)
            if df is None or df.empty:
                self._send_text(200, "O arquivo enviado não é um relatório válido ou não gerou linhas.")
                return

            # Excel (resumo por Item)
            excel_buf = io.BytesIO()
            df.to_excel(excel_buf, index=False)
            excel_bytes = excel_buf.getvalue()

            # PDF Memória de Cálculo
            memoria_pdf_bytes = build_memoria_calculo_pdf(pdf_bytes)

            # ZIP com os dois
            zip_buf = io.BytesIO()
            with zipfile.ZipFile(zip_buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
                z.writestr("relatorio_precos_compoe_comprasgov.xlsx", excel_bytes)
                z.writestr("Memoria_de_Calculo.pdf", memoria_pdf_bytes)

            zip_bytes = zip_buf.getvalue()

            filename = "resultado_extracao.zip"
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(len(zip_bytes)))
            self.end_headers()
            self.wfile.write(zip_bytes)

        except Exception as e:
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
