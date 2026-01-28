import json
import os
import traceback
from datetime import datetime
from urllib.parse import urlparse, parse_qs
from http.server import BaseHTTPRequestHandler

try:
    import psycopg2
except Exception:  # pragma: no cover
    psycopg2 = None


def _send_json(h: BaseHTTPRequestHandler, status: int, payload: dict):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    h.send_response(status)
    h.send_header("Content-Type", "application/json; charset=utf-8")
    h.send_header("Content-Length", str(len(data)))
    h.end_headers()
    h.wfile.write(data)


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            if psycopg2 is None:
                return _send_json(self, 500, {"error": "psycopg2 indisponível"})

            dsn = (os.environ.get("DATABASE_URL") or "").strip()
            if not dsn:
                return _send_json(self, 400, {"error": "DATABASE_URL não configurada"})

            q = parse_qs(urlparse(self.path).query)
            numero_lista = (q.get("numero_lista", [""])[0] or "").strip()
            limit = int((q.get("limit", ["100"])[0] or "100").strip() or "100")
            offset = int((q.get("offset", ["0"])[0] or "0").strip() or "0")
            limit = max(1, min(limit, 500))
            offset = max(0, offset)

            where_sql = ""
            params = []
            if numero_lista:
                where_sql = "WHERE l.numero_lista = %s"
                params.append(numero_lista)

            conn = psycopg2.connect(dsn, sslmode="require")
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        f"SELECT COUNT(*) FROM lista_runs r JOIN listas l ON l.id = r.lista_id {where_sql};",
                        tuple(params),
                    )
                    total = int(cur.fetchone()[0] or 0)

                    cur.execute(
                        (
                            "SELECT l.numero_lista, COALESCE(l.nome_lista, ''), COALESCE(l.processo_sei, ''), COALESCE(l.responsavel_atual, ''), "
                            "r.id::text AS run_id, r.run_number, r.saved_at, r.r2_key_archive_zip, COALESCE(r.sha256_zip, ''), r.size_bytes "
                            "FROM lista_runs r "
                            "JOIN listas l ON l.id = r.lista_id "
                            f"{where_sql} "
                            "ORDER BY r.saved_at DESC "
                            "LIMIT %s OFFSET %s;"
                        ),
                        tuple(params + [limit, offset]),
                    )
                    out = []
                    for row in cur.fetchall():
                        saved_at = row[6]
                        if isinstance(saved_at, datetime):
                            saved_iso = saved_at.isoformat()
                        else:
                            saved_iso = str(saved_at)
                        out.append(
                            {
                                "numero_lista": row[0],
                                "nome_lista": row[1],
                                "processo_sei": row[2],
                                "responsavel_atual": row[3],
                                "run_id": row[4],
                                "run_number": int(row[5] or 0),
                                "saved_at_iso": saved_iso,
                                "r2_key": row[7],
                                "sha256_zip": row[8],
                                "size_bytes": int(row[9]) if row[9] is not None else None,
                            }
                        )

                return _send_json(self, 200, {"total": total, "limit": limit, "offset": offset, "rows": out})
            finally:
                conn.close()

        except Exception as e:
            return _send_json(
                self,
                500,
                {
                    "error": str(e),
                    "trace": traceback.format_exc(),
                },
            )
