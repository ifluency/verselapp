import io
import cgi
import json
import traceback
from urllib.parse import urlparse, parse_qs
from http.server import BaseHTTPRequestHandler

from parser.parser import process_pdf_bytes, build_itens_relatorio


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
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
                self._send_json(200, {"items": [], "message": "Nenhuma linha com Compõe=Sim foi encontrada."})
                return

            itens = build_itens_relatorio(df, payload=None)

            # Resposta enxuta para o front (prévia)
            out_items = []
            for it in itens:
                valores = it.get("valores_brutos") or []
                fontes = it.get("fontes_brutos") or []
                pares = []
                for i, v in enumerate(valores):
                    fonte = fontes[i] if i < len(fontes) else ""
                    pares.append({"idx": i, "valor": v, "fonte": fonte})
                out_items.append(
                    {
                        "item": str(it.get("item")),
                        "catmat": str(it.get("catmat") or ""),
                        "n_bruto": int(it.get("n_bruto") or 0),
                        "n_final": int(it.get("n_final_auto") or 0),
                        "excl_altos": int(it.get("excl_altos") or 0),
                        "excl_baixos": int(it.get("excl_baixos") or 0),
                        "valor_calculado": it.get("valor_auto"),
                        "valores_brutos": pares,
                    }
                )

            self._send_json(200, {"items": out_items})

        except Exception as e:
            tb = traceback.format_exc()
            print("ERROR /api/preview:", str(e))
            print(tb)

            if debug_mode:
                self._send_text(500, f"Erro ao processar:\n{str(e)}\n\nSTACKTRACE:\n{tb}")
            else:
                self._send_text(500, "Falha ao processar. Tente novamente ou use /api/preview?debug=1 para ver detalhes.")

    def do_GET(self):
        self._send_text(405, "Use POST com multipart/form-data (campo 'file').")

    def _send_text(self, status: int, msg: str):
        data = (msg or "").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, status: int, payload: dict):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
