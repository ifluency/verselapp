import os
import json
import traceback
from datetime import datetime, date
from decimal import Decimal
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


def _fmt_date_br(d):
    if not d:
        return ""
    return d.strftime("%d/%m/%Y")


def _pregao_from_id_compra(id_compra):
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


def _item_link(id_compra, numero_item_pncp):
    if not id_compra:
        return ""
    s = str(id_compra).strip()
    if not s:
        return ""
    try:
        n = int(numero_item_pncp) if numero_item_pncp is not None else None
    except Exception:
        n = None
    if n is None:
        return (
            "https://cnetmobile.estaleiro.serpro.gov.br/comprasnet-web/public/compras/"
            f"acompanhamento-compra?compra={s}"
        )
    return (
        "https://cnetmobile.estaleiro.serpro.gov.br/comprasnet-web/public/compras/"
        f"acompanhamento-compra/item/-{n}?compra={s}"
    )


def _to_float(x):
    if x is None:
        return None
    if isinstance(x, Decimal):
        return float(x)
    if isinstance(x, str):
        s = x.strip()
        if not s:
            return None
        s = s.replace("R$", "").replace(" ", "")
        s = s.replace(".", "").replace(",", ".")
        try:
            return float(s)
        except Exception:
            return None
    try:
        return float(x)
    except Exception:
        return None


def _to_int(x):
    if x is None:
        return None
    try:
        return int(x)
    except Exception:
        return None


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        q = parse_qs(urlparse(self.path).query)
        debug_mode = q.get("debug", ["0"])[0] in ("1", "true", "True", "yes", "sim")

        try:
            length = int(self.headers.get("content-length") or 0)
            raw = self.rfile.read(length) if length > 0 else b"{}"
            body = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            body = {}

        catmats = body.get("catmats") or []
        if not isinstance(catmats, list):
            return self._send_json(400, {"error": "Parâmetro 'catmats' inválido. Envie uma lista."})

        cleaned = []
        for c in catmats:
            s = str(c).strip()
            if s.isdigit():
                cleaned.append(s)

        if not cleaned:
            return self._send_json(400, {"error": "Lista de CATMATs vazia ou inválida."})

        try:
            dsn = os.environ.get("DATABASE_URL", "").strip()
            if not dsn:
                return self._send_json(500, {"error": "DATABASE_URL não configurada no ambiente."})

            conn = psycopg2.connect(dsn, sslmode="require")
            try:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        """
                        SELECT DISTINCT ON (i.cod_item_catalogo)
                          i.cod_item_catalogo::text AS catmat,
                          i.id_compra::text AS id_compra,
                          i.numero_item_pncp,
                          i.data_resultado,
                          COALESCE(i.nome_fornecedor,'')::text AS nome_fornecedor,
                          COALESCE(i.situacao_compra_item_nome,'')::text AS situacao_compra_item_nome,
                          i.valor_unitario_estimado,
                          i.valor_unitario_resultado
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
                        (cleaned,),
                    )
                    rows = cur.fetchall() or []
            finally:
                conn.close()

            by_catmat = {c: {"catmat": c, "status": "nao_encontrado"} for c in cleaned}

            for r in rows:
                c = str(r.get("catmat") or "").strip()
                id_compra = str(r.get("id_compra") or "").strip()
                d_res = _as_date(r.get("data_resultado"))
                num_item = _to_int(r.get("numero_item_pncp"))

                v_est = _to_float(r.get("valor_unitario_estimado"))
                v_res = _to_float(r.get("valor_unitario_resultado"))

                status = "ok" if v_res is not None else "fracassado"

                by_catmat[c] = {
                    "catmat": c,
                    "status": status,
                    "data_resultado_iso": d_res.isoformat() if d_res else None,
                    "data_resultado_br": _fmt_date_br(d_res),
                    "id_compra": id_compra,
                    "pregao": _pregao_from_id_compra(id_compra),
                    "numero_item_pncp": num_item,
                    "compra_link": _item_link(id_compra, num_item),
                    "nome_fornecedor": str(r.get("nome_fornecedor") or ""),
                    "situacao_compra_item_nome": str(r.get("situacao_compra_item_nome") or ""),
                    "valor_unitario_estimado_num": v_est,
                    "valor_unitario_resultado_num": v_res,
                }

            return self._send_json(200, {"by_catmat": by_catmat})

        except Exception as e:
            tb = traceback.format_exc()
            print("ERROR /api/ultimo_licitado:", str(e))
            print(tb)
            if debug_mode:
                return self._send_json(500, {"error": str(e), "trace": tb})
            return self._send_json(500, {"error": "Falha ao consultar último valor licitado."})

    def _send_json(self, status, payload):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
