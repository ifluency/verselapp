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


def _item_link(id_compra: str | None, numero_item_pncp) -> str:
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
        # fallback: link do pregão
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
    # Suporte a valores em texto no padrão PT-BR (ex: "R$ 1.234,5600")
    if isinstance(x, str):
        s = x.strip()
        if not s:
            return None
        s = s.replace("R$", "").replace(" ", "")
        # remove separador de milhar e troca vírgula por ponto
        s = s.replace(".", "").replace(",", ".")
        try:
            return float(s)
        except Exception:
            return None
    try:
        return float(x)
    except Exception:
        return None


def _pick(d: dict, *keys, default=""):
    for k in keys:
        if k in d and d[k] is not None and str(d[k]).strip() != "":
            return d[k]
    return default


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        q = parse_qs(urlparse(self.path).query)
        debug_mode = q.get("debug", ["0"])[0] in ("1", "true", "True", "yes", "sim")

        catmat = (q.get("catmat", [""])[0] or "").strip()
        if not catmat:
            payload = {
                "error": "Parâmetro 'catmat' é obrigatório.",
                "usage": "Use GET com ?catmat=XXXXXX",
                "example": "/api/catmat_historico?catmat=455302",
            }
            return self._send_json(200 if debug_mode else 400, payload)

        if not catmat.isdigit():
            payload = {
                "error": "Parâmetro 'catmat' inválido. Deve conter apenas dígitos.",
                "usage": "Use GET com ?catmat=XXXXXX",
                "example": "/api/catmat_historico?catmat=455302",
            }
            return self._send_json(400, payload)

        try:
            dsn = os.environ.get("DATABASE_URL", "").strip()
            if not dsn:
                return self._send_json(500, {"error": "DATABASE_URL não configurada no ambiente."})

            conn = psycopg2.connect(dsn, sslmode="require")
            try:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        """
                        SELECT
                          i.cod_item_catalogo::text AS catmat,
                          i.id_compra::text AS id_compra,
                          i.id_compra_item::text AS id_compra_item,
                          i.numero_item_pncp,
                          i.data_resultado,
                          i.valor_unitario_estimado,
                          i.valor_unitario_resultado,
                          row_to_json(i) AS item_json
                        FROM contratacao_item_pncp_14133 i
                        WHERE i.cod_item_catalogo IS NOT NULL
                          AND i.cod_item_catalogo::text = %s
                          AND i.criterio_julgamento_id_pncp = 1
                        ORDER BY i.data_resultado DESC NULLS LAST,
                                 i.data_atualizacao_pncp DESC NULLS LAST,
                                 i.data_inclusao_pncp DESC NULLS LAST,
                                 i.id_compra DESC,
                                 i.id_compra_item DESC
                        """,
                        (catmat,),
                    )
                    rows = cur.fetchall() or []
            finally:
                conn.close()

            out_rows = []
            seq = 1
            for r in rows:
                id_compra = str(r.get("id_compra") or "").strip()
                num_item = r.get("numero_item_pncp")
                d_res = _as_date(r.get("data_resultado"))

                item_json = r.get("item_json") or {}
                fornecedor = str(_pick(item_json, "nome_fornecedor", "nomeFornecedor", default="") or "").strip()
                situacao = str(_pick(item_json, "situacao_compra_item_nome", "situacaoCompraItemNome", default="") or "").strip()

                v_est = _to_float(r.get("valor_unitario_estimado"))
                v_res = _to_float(r.get("valor_unitario_resultado"))

                out_rows.append(
                    {
                        "seq": seq,
                        "data_resultado_iso": d_res.isoformat() if d_res else None,
                        "data_resultado_br": _fmt_date_br(d_res),
                        "pregao": _pregao_from_id_compra(id_compra),
                        "numero_item_pncp": num_item,
                        "situacao": situacao,
                        "fornecedor": fornecedor,
                        "link": _item_link(id_compra, num_item),
                        "valor_estimado_num": v_est,
                        "valor_licitado_num": v_res,  # null => fracassado no front
                    }
                )
                seq += 1

            return self._send_json(200, {"catmat": catmat, "rows": out_rows, "count": len(out_rows)})

        except Exception as e:
            tb = traceback.format_exc()
            print("ERROR /api/catmat_historico:", str(e))
            print(tb)
            if debug_mode:
                return self._send_text(500, f"Erro ao consultar:\n{str(e)}\n\nSTACKTRACE:\n{tb}")
            return self._send_text(500, f"Falha ao consultar histórico PNCP: {str(e)}")

    def do_POST(self):
        return self._send_text(405, "Use GET com ?catmat=XXXXXX")

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
