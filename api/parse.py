import io
import cgi
import traceback
import zipfile
from urllib.parse import urlparse, parse_qs
from http.server import BaseHTTPRequestHandler

import pandas as pd

from parser.parser import process_pdf_bytes, gerar_resumo, build_memoria_calculo_pdf_bytes


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        # debug via querystring ?debug=1
        q = parse_qs(urlparse(self.path).query)
        debug_mode = q.get("debug", ["0"])[0] in ("1", "true", "True", "yes", "sim")

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
                self._send_text(200, "Nenhuma linha com Compõe=Sim foi encontrada no arquivo enviado.")
                return

            # Excel
            df_resumo = gerar_resumo(df)
            excel_out = io.BytesIO()
            with pd.ExcelWriter(excel_out, engine="openpyxl") as writer:
                df.to_excel(writer, index=False, sheet_name="Dados")
                df_resumo.to_excel(writer, index=False, sheet_name="Resumo")
            excel_out.seek(0)
            xlsx_bytes = excel_out.read()

            # PDF (Memoria de Calculo)
            pdf_memoria_bytes = build_memoria_calculo_pdf_bytes(df)

            # ZIP com os dois arquivos
            zip_out = io.BytesIO()
            with zipfile.ZipFile(zip_out, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("relatorio_precos_compoe_sim.xlsx", xlsx_bytes)
                zf.writestr("Memoria_de_Calculo.pdf", pdf_memoria_bytes)

            zip_out.seek(0)
            zip_bytes = zip_out.read()

            filename = "resultado.zip"
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(len(zip_bytes)))
            self.end_headers()
            self.wfile.write(zip_bytes)

        except Exception as e:
            tb = traceback.format_exc()

            # Console do Vercel (Logs)
            print("ERROR /api/parse:", str(e))
            print(tb)

            if debug_mode:
                self._send_text(500, f"Erro ao processar:\n{str(e)}\n\nSTACKTRACE:\n{tb}")
            else:
                self._send_text(500, "Falha ao processar. Tente novamente ou use /api/parse?debug=1 para ver detalhes.")

    def do_GET(self):
        self._send_text(405, "Use POST com multipart/form-data (campo 'file').")

    def _send_text(self, status: int, msg: str):
        data = (msg or "").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
