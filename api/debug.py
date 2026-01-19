import io
import cgi
from http.server import BaseHTTPRequestHandler

from parser.parser import process_pdf_bytes, build_debug_audit_text


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            content_type = self.headers.get("content-type", "")
            if "multipart/form-data" not in content_type:
                return self._send_text(400, "Envie multipart/form-data com campo 'file'.")

            content_length = int(self.headers.get("content-length", "0"))
            if content_length <= 0:
                return self._send_text(400, "Corpo vazio.")

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
            df = process_pdf_bytes(pdf_bytes)

            txt = build_debug_audit_text(df, max_items=5, min_n=5)

            data = txt.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="debug_audit.txt"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        except Exception as e:
            self._send_text(500, f"Erro no debug: {str(e)}")

    def do_GET(self):
        self._send_text(405, "Use POST com multipart/form-data (campo 'file').")

    def _send_text(self, status: int, msg: str):
        data = (msg or "").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
