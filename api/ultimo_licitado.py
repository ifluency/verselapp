import json
import os
import traceback
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

import psycopg2
from psycopg2.extras import RealDictCursor


def _as_date(v):
    """Converte o valor retornado pelo Postgres para date (quando aplicável)."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    # Em geral o driver já retorna date/datetime. Se vier string, não convertemos.
    return None


def _fmt_br(d) -> str:
    if not d:
        return ""
    return d.strftime("%d/%m/%Y")


def _normalize_catmat(x):
    s = str(x or "").strip()
    if s.isdigit() and len(s) == 6:
        return s
    digits = "".join(ch for ch in s if ch.isdigit())
    if digits.isdigit() and len(digits) == 6:
        return digits
    return None


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        q = parse_qs(urlparse(self.path).query)
        debug_mode = q.get("debug", ["0"])[0] in ("1", "true", "True", "yes", "sim")

        try:
            length = int(self.headers.get("content-length", "0") or "0")
            raw = self.rfile.read(length) if length > 0 else b"{}"

            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                payload = {}

            catmats_in = payload.get("catmats") or []
            if not isinstance(catmats_in, list):
                return self._send_json(400, {"error": "Campo 'catmats' deve ser uma lista."})

            catmats = []
            for c in catmats_in:
                norm = _normalize_catmat(c)
                if norm:
                    catmats.append(norm)

            # unique preservando ordem
            catmats = list(dict.fromkeys(catmats))

            if not catmats:
                return self._send_json(200, {"by_catmat": {}, "count": 0})

            dsn = os.environ.get("DATABASE_URL", "").strip()
            if not dsn:
                return self._send_json(500, {"error": "DATABASE_URL não configurada no ambiente."})

            conn = psycopg2.connect(dsn, sslmode="require")
            try:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        """
                        SELECT
                          cod_item_catalogo::text AS catmat,
                          data_resultado,
                          valor_unitario_resultado
                        FROM vw_catmat_preco_ultimo
                        WHERE cod_item_catalogo::text = ANY(%s)
                        """,
                        (catmats,),
                    )
                    rows = cur.fetchall() or []
            finally:
                conn.close()

            by_catmat = {}
            for c in catmats:
                by_catmat[c] = {
                    "catmat": c,
                    "status": "nao_encontrado",
                    "valor_unitario_resultado_num": None,
                    "data_resultado_iso": None,
                    "data_resultado_br": "",
                }

            for r in rows:
                c = str(r.get("catmat") or "").strip()
                d = _as_date(r.get("data_resultado"))
                v = r.get("valor_unitario_resultado")

                try:
                    vnum = float(v) if v is not None else None
                except Exception:
                    vnum = None

                status = "ok" if vnum is not None else "fracassado"
                by_catmat[c] = {
                    "catmat": c,
                    "status": status,
                    "valor_unitario_resultado_num": vnum,
                    "data_resultado_iso": d.isoformat() if d else None,
                    "data_resultado_br": _fmt_br(d),
                }

            return self._send_json(200, {"by_catmat": by_catmat, "count": len(by_catmat)})

        except Exception as e:
            tb = traceback.format_exc()
            print("ERROR /api/ultimo_licitado:", str(e))
            print(tb)
            if debug_mode:
                return self._send_text(500, f"Erro ao consultar base PNCP:\n{str(e)}\n\nSTACKTRACE:\n{tb}")
            return self._send_text(
                500,
                "Falha ao consultar base PNCP. Tente novamente ou use /api/ultimo_licitado?debug=1.",
            )

    def do_GET(self):
        return self._send_text(405, 'Use POST com JSON: {"catmats": ["455302", ...]}')

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
