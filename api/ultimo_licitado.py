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


def _to_float(x):
    try:
        return float(x) if x is not None else None
    except Exception:
        return None


def _pick(d: dict, *keys, default=""):
    for k in keys:
        if k in d and d[k] is not None and str(d[k]).strip() != "":
            return d[k]
    return default


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
                    # usa row_to_json(i) para não quebrar se algum campo “extra” variar
                    cur.execute(
                        """
                        SELECT DISTINCT ON (i.cod_item_catalogo)
                          i.cod_item_catalogo::text AS catmat,
                          i.id_compra::text AS id_compra,
                          i.data_resultado,
                          i.valor_unitario_estimado,
                          i.valor_unitario_resultado,
                          row_to_json(i) AS item_json
                        FROM contratacao_item_pncp_14133 i
                        WHERE i.cod_item_catalogo IS NOT NULL
                          AND i.cod_item_catalogo::text = ANY(%s)
                          AND i.criterio_julgamento_id_pncp = 1
                        ORDER BY i.cod_item_catalogo,
                                 i.data_resultado DESC NULLS LAST,
                                 i.data_atualizacao_pncp DESC NULLS LAST,
                                 i.data_inclusao_pncp DESC NULLS LAST,
                                 i.id_compra DESC,
                                 i.id_compra_item DESC
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

                item_json = r.get("item_json") or {}
                # tenta pegar em diferentes chaves (se houver variação)
                fornecedor = str(_pick(item_json, "nome_fornecedor", "nomeFornecedor", default="") or "").strip()

                v_est = _to_float(r.get("valor_unitario_estimado"))
                v_res = _to_float(r.get("valor_unitario_resultado"))
                status = "ok" if v_res is not None else "fracassado"

                by_catmat[c] = {
                    "catmat": c,
                    "status": status,
                    "data_resultado_iso": (d.isoformat() if d else None),
                    "data_resultado_br": _fmt_date_br(d),
                    "id_compra": id_compra,
                    "pregao": _pregao_from_id_compra(id_compra),
                    "compra_link": _compra_link(id_compra),
                    "nome_fornecedor": fornecedor,
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
            return self._send_text(500, f"Falha ao consultar base PNCP: {str(e)}")

    def do_GET(self):
        return self._send_text(405, "Use POST com JSON: {\"catmats\": [\"455302\", ...]}")

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
