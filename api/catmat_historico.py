import os
import json
import traceback
from datetime import datetime, date
from decimal import Decimal
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import psycopg2
from psycopg2.extras import RealDictCursor


def _json_default(o):
    # Evita erro: Decimal não é serializável em JSON
    if isinstance(o, Decimal):
        return float(o)
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    return str(o)


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

    base = "https://cnetmobile.estaleiro.serpro.gov.br/comprasnet-web/public/compras/acompanhamento-compra"
    if n is None:
        return f"{base}?compra={s}"

    # Formato solicitado: /item/{n}?compra={id_compra}
    return f"{base}/item/{n}?compra={s}"



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
                          COALESCE(i.descricao_resumida,'')::text AS descricao_resumida,
                          COALESCE(i.material_ou_servico,'')::text AS material_ou_servico,
                          COALESCE(i.unidade_medida,'')::text AS unidade_medida,
                          i.id_compra::text AS id_compra,
                          i.id_compra_item::text AS id_compra_item,
                          i.numero_controle_pncp_compra::text AS numero_controle_pncp_compra,
                          c.codigo_modalidade,
                          i.data_resultado,
                          i.numero_item_pncp,
                          i.quantidade,
                          i.valor_unitario_estimado,
                          i.valor_unitario_resultado,
                          i.data_atualizacao_pncp,
                          i.data_inclusao_pncp,
                          COALESCE(i.nome_fornecedor,'')::text AS nome_fornecedor,
                          COALESCE(i.situacao_compra_item_nome,'')::text AS situacao_compra_item_nome
                        FROM contratacao_item_pncp_14133 i
                        LEFT JOIN contratacao_pncp_14133 c ON c.id_compra = i.id_compra
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
            for r in rows:
                id_compra = str(r.get("id_compra") or "").strip()
                d_res = _as_date(r.get("data_resultado"))
                pregao = _pregao_from_id_compra(id_compra)
                num_item = _to_int(r.get("numero_item_pncp"))

                v_est = _to_float(r.get("valor_unitario_estimado"))
                v_res = _to_float(r.get("valor_unitario_resultado"))

                out_rows.append(
                    {
                        "catmat": str(r.get("catmat") or ""),
                        "descricao_resumida": str(r.get("descricao_resumida") or ""),
                        "material_ou_servico": str(r.get("material_ou_servico") or ""),
                        "unidade_medida": str(r.get("unidade_medida") or ""),
                        "id_compra": id_compra,
                        "id_compra_item": str(r.get("id_compra_item") or ""),
                        "numero_controle_pncp_compra": str(r.get("numero_controle_pncp_compra") or ""),
                        "codigo_modalidade": r.get("codigo_modalidade"),
                        "data_resultado_iso": d_res.isoformat() if d_res else None,
                        "data_resultado_br": _fmt_date_br(d_res),
                        "pregao": pregao,
                        "numero_item_pncp": num_item,
                        "quantidade": r.get("quantidade"),
                        "nome_fornecedor": str(r.get("nome_fornecedor") or ""),
                        "situacao_compra_item_nome": str(r.get("situacao_compra_item_nome") or ""),
                        "compra_link": _item_link(id_compra, num_item),
                        "valor_unitario_estimado_num": v_est,
                        "valor_unitario_resultado_num": v_res,
                        "resultado_status": "fracassado" if v_res is None else "ok",
                    }
                )

            return self._send_json(200, {"catmat": catmat, "rows": out_rows})

        except Exception as e:
            tb = traceback.format_exc()
            print("ERROR /api/catmat_historico:", str(e))
            print(tb)
            if debug_mode:
                return self._send_text(500, f"Erro ao consultar:\n{str(e)}\n\nSTACKTRACE:\n{tb}")
            return self._send_text(500, f"Falha ao consultar histórico PNCP: {str(e)}")

    def _send_text(self, status, msg):
        data = (msg or "").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, status, payload):
        data = json.dumps(payload, ensure_ascii=False, default=_json_default).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
