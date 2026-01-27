import os
import json
import traceback
from datetime import datetime, date
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import psycopg2
from psycopg2.extras import RealDictCursor


def _as_date(v):
    if v is None:
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    try:
        return datetime.fromisoformat(str(v)).date()
    except Exception:
        return None


def _fmt_date_br(d: date | None) -> str:
    if not d:
        return ""
    return d.strftime("%d/%m/%Y")


def _pregao_from_id_compra(id_compra: str | None) -> str:
    """Deriva pregão a partir do id_compra.

    Regra prática:
      - Ano = últimos 4 dígitos
      - Número = 5 dígitos antes do ano
      - Se número começar com 9, remove o 9 e usa os 4 restantes
      - Formata como XXX/AAAA (padding)
    """
    if not id_compra:
        return ""
    s = str(id_compra).strip()
    if len(s) < 9:
        return ""
    year = s[-4:]
    num5 = s[-9:-4]
    if not (year.isdigit() and num5.isdigit()):
        return ""
    if num5.startswith("9"):
        num = num5[1:]
    else:
        num = num5
    try:
        return f"{int(num):03d}/{int(year):04d}"
    except Exception:
        return ""


def _compra_link(id_compra: str | None) -> str:
    if not id_compra:
        return ""
    s = str(id_compra).strip()
    if not s:
        return ""
    return (
        "https://cnetmobile.estaleiro.serpro.gov.br/comprasnet-web/public/compras/"
        f"acompanhamento-compra?compra={s}"
    )


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

            # Normaliza catmat: só dígitos; mantém strings numéricas com 6+ dígitos
            catmats: list[str] = []
            for c in catmats_in:
                s = str(c).strip()
                if s.isdigit():
                    catmats.append(s)
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
                          id_compra::text AS id_compra,
                          data_resultado,
                          valor_unitario_estimado,
                          valor_unitario_resultado,
                          nome_fornecedor
                        FROM vw_catmat_preco_ultimo
                        WHERE cod_item_catalogo::text = ANY(%s)
                        """,
                        (catmats,),
                    )
                    rows = cur.fetchall() or []
            finally:
                conn.close()

            # Default: não encontrado
            by_catmat: dict[str, dict] = {}
            for c in catmats:
                by_catmat[c] = {
                    "catmat": c,
                    "status": "nao_encontrado",
                    "data_resultado_iso": None,
                    "data_resultado_br": "",
                    "id_compra": "",
                    "pregao": "",
                    "compra_link": "",
                    "nome_fornecedor": "",
                    "valor_unitario_estimado_num": None,
                    "valor_unitario_resultado_num": None,
                }

            for r in rows:
                c = str(r.get("catmat") or "").strip()
                id_compra = str(r.get("id_compra") or "").strip()
                d = _as_date(r.get("data_resultado"))

                def _to_float(x):
                    try:
                        return float(x) if x is not None else None
                    except Exception:
                        return None

                v_est = _to_float(r.get("valor_unitario_estimado"))
                v_res = _to_float(r.get("valor_unitario_resultado"))
                forn = str(r.get("nome_fornecedor") or "").strip()

                status = "ok" if v_res is not None else "fracassado"

                by_catmat[c] = {
                    "catmat": c,
                    "status": status,
                    "data_resultado_iso": (d.isoformat() if d else None),
                    "data_resultado_br": _fmt_date_br(d),
                    "id_compra": id_compra,
                    "pregao": _pregao_from_id_compra(id_compra),
                    "compra_link": _compra_link(id_compra),
                    "nome_fornecedor": forn,
                    "valor_unitario_estimado_num": v_est,
                    "valor_unitario_resultado_num": v_res,
                }

            return self._send_json(200, {"by_catmat": by_catmat, "count": len(by_catmat)})

        except Exception as e:
            tb = traceback.format_exc()
            print("ERROR /api/ultimo_licitado:", str(e))
            print(tb)
            if debug_mode:
                return self._send_text(500, f"Erro ao consultar:\n{str(e)}\n\nSTACKTRACE:\n{tb}")
            return self._send_text(
                500,
                "Falha ao consultar base PNCP. Tente novamente ou use /api/ultimo_licitado?debug=1.",
            )

    def do_GET(self):
        return self._send_text(405, "Use POST com JSON: {\"catmats\": [\"455302\", ...]} ")

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
