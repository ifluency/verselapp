import io
import cgi
import pandas as pd
from http.server import BaseHTTPRequestHandler

# Importa a lógica do arquivo parser/__init__.py
from parser import process_pdf_bytes_debug

class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            # 1. Validação básica do Request
            content_type = self.headers.get("content-type", "")
            if "multipart/form-data" not in content_type:
                return self._send_text(400, "Envie multipart/form-data com campo 'file'.")

            content_length = int(self.headers.get("content-length", "0"))
            if content_length <= 0:
                return self._send_text(400, "Corpo vazio.")

            # 2. Leitura do PDF enviado
            body = self.rfile.read(content_length)
            environ = {
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": content_type,
                "CONTENT_LENGTH": str(content_length),
            }
            fp = io.BytesIO(body)
            form = cgi.FieldStorage(fp=fp, environ=environ, keep_blank_values=True)

            if "file" not in form:
                return self._send_text(400, "Campo 'file' ausente.")

            pdf_bytes = form["file"].file.read()
            if not pdf_bytes:
                return self._send_text(400, "Arquivo 'file' vazio.")

            # 3. Processamento (Lógica corrigida no __init__.py)
            # A variável 'debug_records' é ignorada pois queremos o Excel final
            df, _ = process_pdf_bytes_debug(pdf_bytes)

            # 4. Geração do Excel em Memória
            output = io.BytesIO()
            # Engine 'openpyxl' é necessária para escrever xlsx
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                df.to_excel(writer, index=False, sheet_name='Fornecedores')
            
            excel_data = output.getvalue()

            # 5. Envio da Resposta (Download do arquivo)
            self.send_response(200)
            # MIME type correto para Excel .xlsx
            self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            self.send_header("Content-Disposition", 'attachment; filename="fornecedores_corrigido.xlsx"')
            self.send_header("Content-Length", str(len(excel_data)))
            self.end_headers()
            
            self.wfile.write(excel_data)

        except Exception as e:
            # Em caso de erro, retorna texto para facilitar o debug
            return self._send_text(500, f"Erro interno: {repr(e)}")

    def do_GET(self):
        self._send_text(405, "Use POST com multipart/form-data (campo 'file').")

    def _send_text(self, status: int, msg: str):
        data = (msg or "").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
