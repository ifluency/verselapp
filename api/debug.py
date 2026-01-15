import io
import cgi
from http.server import BaseHTTPRequestHandler
import pdfplumber


def dump_first_pages(pdf_bytes: bytes, pages=3, max_lines=260) -> str:
    out = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        n_pages = min(pages, len(pdf.pages))
        for p in range(n_pages):
            out.append("=" * 70)
            out.append(f"PAGE {p+1}")
            out.append("=" * 70)

            txt = pdf.pages[p].extract_text(layout=True) or ""
            lines = txt.splitlines()
            out.append(f"Total linhas extraídas: {len(lines)}")

            for i, line in enumerate(lines[:max_lines]):
                out.append(f"{i:03d} | {line}")

            out.append("")  # linha em branco entre páginas
    return "\n".join(out)


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
            txt = dump_first_pages(pdf_bytes, pages=3, max_lines=320)

            data = txt.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="dump.txt"')
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
