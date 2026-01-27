import json
import os
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

import psycopg2
import psycopg2.extras


def _to_brl_2(v):
    """Return a pt-BR display string with 2 decimal places and comma decimal separator."""
    if v is None:
        return None
    try:
        d = Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except Exception:
        return None
    # thousands separator '.' and decimal ','
    s = f"{d:,.2f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def _to_ddmmyyyy(d):
    if d is None:
        return None
    if isinstance(d, str):
        try:
            # Accept ISO date/time strings
            d = datetime.fromisoformat(d.replace("Z", "+00:00"))
        except Exception:
            return None
    if isinstance(d, datetime):
        d = d.date()
    if isinstance(d, date):
        return d.strftime("%d/%m/%Y")
    return None


def _send_json(handler: BaseHTTPRequestHandler, status: int, payload: dict):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _db_conn():
    dsn = os.getenv("DATABASE_URL") or os.getenv("NEON_DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL env var is required")
    return psycopg2.connect(dsn)


LATEST_SQL = """
with base as (
  select
    i.cod_item_catalogo as catmat,
    i.id_compra,
    i.numero_item_pncp,
    i.descricao_resumida,
    i.situacao_compra_item_nome,
    i.tem_resultado,
    i.nome_fornecedor,
    i.valor_unitario_estimado,
    i.valor_unitario_resultado,
    i.data_resultado,
    i.data_atualizacao_pncp,
    -- pregão: últimos 3 dígitos do número do pregão + ano
    right(substring(i.id_compra from length(i.id_compra)-8 for 5), 3) || '/' || right(i.id_compra, 4) as pregao,
    'https://cnetmobile.estaleiro.serpro.gov.br/comprasnet-web/public/compras/acompanhamento-compra?compra=' || i.id_compra as compra_url,
    coalesce(i.data_resultado::date, i.data_atualizacao_pncp::date, i.data_inclusao_pncp::date) as dt_ord
  from contratacao_item_pncp_14133 i
  where i.cod_item_catalogo = any(%s)
),
latest_any as (
  select distinct on (catmat) *
  from base
  order by catmat, dt_ord desc nulls last, data_atualizacao_pncp desc nulls last
),
latest_result as (
  select distinct on (catmat) *
  from base
  where valor_unitario_resultado is not null
  order by catmat, data_resultado desc nulls last, data_atualizacao_pncp desc nulls last
)
select
  a.catmat,
  a.id_compra as id_compra_any,
  a.pregao as pregao_any,
  a.compra_url as compra_url_any,
  a.numero_item_pncp as numero_item_pncp_any,
  a.descricao_resumida as descricao_resumida_any,
  a.situacao_compra_item_nome as situacao_compra_item_nome_any,
  a.tem_resultado as tem_resultado_any,
  a.nome_fornecedor as nome_fornecedor_any,
  a.valor_unitario_estimado as valor_unitario_estimado_any,
  a.data_resultado as data_resultado_any,

  r.id_compra as id_compra_result,
  r.pregao as pregao_result,
  r.compra_url as compra_url_result,
  r.numero_item_pncp as numero_item_pncp_result,
  r.nome_fornecedor as nome_fornecedor_result,
  r.valor_unitario_resultado as valor_unitario_resultado_result,
  r.data_resultado as data_resultado_result
from latest_any a
left join latest_result r using (catmat)
order by a.catmat;
"""


HISTORY_SQL = """
select
  i.cod_item_catalogo as catmat,
  i.id_compra,
  right(substring(i.id_compra from length(i.id_compra)-8 for 5), 3) || '/' || right(i.id_compra, 4) as pregao,
  'https://cnetmobile.estaleiro.serpro.gov.br/comprasnet-web/public/compras/acompanhamento-compra?compra=' || i.id_compra as compra_url,
  i.numero_item_pncp,
  i.descricao_resumida,
  i.situacao_compra_item_nome,
  i.tem_resultado,
  i.nome_fornecedor,
  i.valor_unitario_estimado,
  i.valor_unitario_resultado,
  i.data_resultado::date as data_resultado
from contratacao_item_pncp_14133 i
where i.cod_item_catalogo = %s
order by i.data_resultado desc nulls last, i.data_atualizacao_pncp desc nulls last;
"""


def _latest_payload(rows):
    out = {}
    for r in rows:
        catmat = str(r["catmat"]) if r.get("catmat") is not None else None
        if not catmat:
            continue

        estimado_num = r.get("valor_unitario_estimado_any")
        estimado_disp = _to_brl_2(estimado_num)

        licitado_num = r.get("valor_unitario_resultado_result")
        licitado_disp = _to_brl_2(licitado_num) if licitado_num is not None else "Fracassado"

        # prefer data/pregão/fornecedor do resultado, se existir; caso contrário, do "latest_any"
        pregao = r.get("pregao_result") or r.get("pregao_any")
        fornecedor = r.get("nome_fornecedor_result") or r.get("nome_fornecedor_any")
        compra_url = r.get("compra_url_result") or r.get("compra_url_any")

        data_res = r.get("data_resultado_result") or r.get("data_resultado_any")

        out[catmat] = {
            "catmat": int(catmat),
            "pregao": pregao,
            "nome_fornecedor": fornecedor,
            "compra_url": compra_url,
            "data_resultado": str(data_res.date()) if isinstance(data_res, datetime) else str(data_res) if data_res else None,
            "data_resultado_display": _to_ddmmyyyy(data_res),
            "valor_estimado_num": float(estimado_num) if estimado_num is not None else None,
            "valor_estimado_display": estimado_disp,
            "valor_licitado_num": float(licitado_num) if licitado_num is not None else None,
            "valor_licitado_display": licitado_disp,
        }
    return out


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        """Batch latest lookup.

        Body: {"catmats": [437118, ...]}
        """
        try:
            length = int(self.headers.get("content-length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            body = json.loads(raw.decode("utf-8") or "{}")
            catmats = body.get("catmats") or []
            catmats = [int(x) for x in catmats if str(x).strip().isdigit()]
            catmats = list(dict.fromkeys(catmats))
            if not catmats:
                return _send_json(self, 200, {"items": {}})

            with _db_conn() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(LATEST_SQL, (catmats,))
                    rows = cur.fetchall()

            return _send_json(self, 200, {"items": _latest_payload(rows)})
        except Exception as e:
            return _send_json(self, 500, {"error": str(e)})

    def do_GET(self):
        """History lookup.

        Query: ?catmat=437118
        """
        try:
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            catmat = (qs.get("catmat") or [None])[0]
            if not catmat or not str(catmat).strip().isdigit():
                return _send_json(self, 400, {"error": "missing catmat"})
            catmat_i = int(catmat)

            with _db_conn() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(HISTORY_SQL, (catmat_i,))
                    hist = cur.fetchall()

                    # also return latest summary for convenience
                    cur.execute(LATEST_SQL, ([catmat_i],))
                    latest_rows = cur.fetchall()

            latest_map = _latest_payload(latest_rows)
            latest = latest_map.get(str(catmat_i))

            history = []
            for r in hist:
                licitado_num = r.get("valor_unitario_resultado")
                licitado_disp = _to_brl_2(licitado_num) if licitado_num is not None else "Fracassado"
                estimado_num = r.get("valor_unitario_estimado")
                estimado_disp = _to_brl_2(estimado_num)
                dr = r.get("data_resultado")
                history.append(
                    {
                        "catmat": r.get("catmat"),
                        "data_resultado": str(dr) if dr else None,
                        "data_resultado_display": _to_ddmmyyyy(dr),
                        "pregao": r.get("pregao"),
                        "numero_item_pncp": r.get("numero_item_pncp"),
                        "valor_estimado_num": float(estimado_num) if estimado_num is not None else None,
                        "valor_estimado_display": estimado_disp,
                        "valor_licitado_num": float(licitado_num) if licitado_num is not None else None,
                        "valor_licitado_display": licitado_disp,
                        "descricao_resumida": r.get("descricao_resumida"),
                        "situacao_compra_item_nome": r.get("situacao_compra_item_nome"),
                        "tem_resultado": r.get("tem_resultado"),
                        "nome_fornecedor": r.get("nome_fornecedor"),
                        "compra_url": r.get("compra_url"),
                        "id_compra": r.get("id_compra"),
                    }
                )

            return _send_json(self, 200, {"catmat": catmat_i, "latest": latest, "history": history})
        except Exception as e:
            return _send_json(self, 500, {"error": str(e)})
