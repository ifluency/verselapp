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


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        q = parse_qs(urlparse(self.path).query)
        debug_mode = q.get("debug", ["0"])[0] in ("1", "true", "True", "yes", "sim")

        catmat = (q.get("catmat", [""])[0] or "").strip()

        # Se não veio catmat, retorna instrução de uso (especialmente útil no debug)
        if not catmat:
            payload = {
                "error": "Parâmetro 'catmat' é obrigatório.",
                "usage": "Use GET com ?catmat=XXXXXX",
                "example": "/api/catmat_historico?catmat=455302",
            }
            # No debug, devolve 200 para você poder ver a mensagem sem parecer “erro do sistema”
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
                          cod_item_catalogo::text AS catmat,
                          descricao_resumida,
                          material_ou_servico,
                          unidade_medida,
                          id_compra::text AS id_compra,
                          id_compra_item::text AS id_compra_item,
                          numero_controle_pncp_compra,
                          codigo_modalidade,
                          data_publicacao_pncp,
                          data_resultado,
                          quantidade,
                          valor_unitario_estimado,
                          valor_unitario_resultado,
                          nome_fornecedor,
                          situacao_compra_item_nome,
                          data_atualizacao_pncp,
                          data_inclusao_pncp
                        FROM vw_catmat_preco_historico
                        WHERE cod_item_catalogo::text = %s
                        ORDER BY data_resultado DESC NULLS LAST,
                                 data_atualizacao_pncp DESC NULLS LAST,
                                 data_inclusao_pncp DESC NULLS LAST,
                                 id_compra DESC,
                                 id_compra_item DESC
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
                d_pub = _as_date(r.get("data_publicacao_pncp"))
                v_est = _to_float(r.get("valor_unitario_estimado"))
                v_res = _to_float(r.get("valor_unitario_resultado"))

                out_rows.append(
                    {
                        "catmat": str(r.get("catmat") or "").strip(),
                        "descricao_resumida": str(r.get("descricao_resumida") or "").strip(),
                        "material_ou_servico": str(r.get("material_ou_servico") or "").strip(),
                        "unidade_medida": str(r.get("unidade_medida") or "").strip(),
                        "id_compra": id_compra,
                        "id_compra_item": str(r.get("id_compra_item") or "").strip(),
                        "numero_controle_pncp_compra": str(r.get("numero_controle_pncp_compra") or "").strip(),
                        "codigo_modalidade": r.get("codigo_modalidade"),
                        "data_publicacao_pncp_iso": d_pub.isoformat() if d_pub else None,
                        "data_publicacao_pncp_br": _fmt_date_br(d_pub),
                        "data_resultado_iso": d_res.isoformat() if d_res else None,
                        "data_resultado_br": _fmt_date_br(d_res),
                        "pregao": _pregao_from_id_compra(id_compra),
                        "quantidade": r.get("quantidade"),
                        "valor_unitario_estimado_num": v_est,
                        "valor_unitario_resultado_num": v_res,
                        "resultado_status": "ok" if v_res is not None else "fracassado",
                        "nome_fornecedor": str(r.get("nome_fornecedor") or "").strip(),
                        "situacao_compra_item_nome": str(r.get("situacao_compra_item_nome") or "").strip(),
                        "compra_link": _compra_link(id_compra),
                    }
                )

            return self._send_json(200, {"catmat": catmat, "rows": out_rows, "count": len(out_rows)})

        except Exception as e:
            tb = traceback.format_exc()
            print("ERROR /api/catmat_historico:", str(e))
            print(tb)
            if debug_mode:
                return self._send_text(500, f"Erro ao consultar:\n{str(e)}\n\nSTACKTRACE:\n{tb}")
            return self._send_text(
                500,
                "Falha ao consultar histórico PNCP. Tente novamente ou use /api/catmat_historico?debug=1.",
            )

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
