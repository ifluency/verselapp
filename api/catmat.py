import json
import re
import traceback
from http.server import BaseHTTPRequestHandler
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError


BASE_URL = "https://dadosabertos.compras.gov.br/modulo-material/4_consultarItemMaterial?codigoItem="


_RE_DIGITS = re.compile(r"\D+")


def _only_digits(s: str) -> str:
    return _RE_DIGITS.sub("", (s or "").strip())


def _fetch_one(code: str, timeout_s: float = 15.0) -> dict:
    codigo_item = _only_digits(code)
    if not codigo_item:
        return {
            "codigoItem": str(code),
            "ok": False,
            "statusItem": None,
            "descricaoItem": "",
            "error": "Código inválido",
        }

    url = f"{BASE_URL}{codigo_item}"
    req = Request(url, method="GET", headers={"Accept": "application/json"})

    try:
        with urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            data = json.loads(raw or "{}")

        resultado = data.get("resultado") or []
        first = resultado[0] if isinstance(resultado, list) and resultado else None
        status_item = None
        desc = ""
        if isinstance(first, dict):
            status_item = first.get("statusItem")
            desc = str(first.get("descricaoItem") or "")

        if not isinstance(status_item, bool):
            return {
                "codigoItem": codigo_item,
                "ok": False,
                "statusItem": None,
                "descricaoItem": "",
                "error": "Sem retorno",
            }

        return {
            "codigoItem": codigo_item,
            "ok": True,
            "statusItem": status_item,
            "descricaoItem": desc,
        }

    except HTTPError as e:
        return {
            "codigoItem": codigo_item,
            "ok": False,
            "statusItem": None,
            "descricaoItem": "",
            "error": f"HTTP {getattr(e, 'code', '')}".strip(),
        }
    except URLError as e:
        return {
            "codigoItem": codigo_item,
            "ok": False,
            "statusItem": None,
            "descricaoItem": "",
            "error": f"Erro de rede: {getattr(e, 'reason', e)}",
        }
    except Exception as e:
        return {
            "codigoItem": codigo_item,
            "ok": False,
            "statusItem": None,
            "descricaoItem": "",
            "error": str(e),
        }


def _unique_keep_order(codes: list[str]) -> list[str]:
    seen = set()
    out = []
    for c in codes:
        d = _only_digits(c)
        if not d:
            continue
        if d in seen:
            continue
        seen.add(d)
        out.append(d)
    return out


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            content_length = int(self.headers.get("content-length", "0") or "0")
            body = self.rfile.read(content_length) if content_length > 0 else b""

            try:
                payload = json.loads((body or b"{}").decode("utf-8", errors="replace"))
            except Exception:
                payload = {}

            codes_raw = payload.get("codes")
            if not isinstance(codes_raw, list):
                return self._send_json(400, {"error": "Campo 'codes' deve ser uma lista."})

            codes = [str(x or "").strip() for x in codes_raw if str(x or "").strip()]
            if not codes:
                return self._send_json(400, {"error": "Nenhum código informado."})

            unique_codes = _unique_keep_order(codes)
            if not unique_codes:
                return self._send_json(400, {"error": "Nenhum código válido informado."})

            # Concurrency pequena para não estourar a API externa.
            limit = 6
            results = []
            with ThreadPoolExecutor(max_workers=min(limit, len(unique_codes))) as ex:
                futs = {ex.submit(_fetch_one, c): c for c in unique_codes}
                for fut in as_completed(futs):
                    results.append(fut.result())

            # Reordena para seguir a ordem original (crescente pela posição em unique_codes)
            order = {c: i for i, c in enumerate(unique_codes)}
            results.sort(key=lambda r: order.get(str(r.get("codigoItem")), 10**9))

            return self._send_json(200, {"results": results})

        except Exception as e:
            tb = traceback.format_exc()
            print("ERROR /api/catmat:", str(e))
            print(tb)
            return self._send_json(500, {"error": "Falha interna ao consultar CATMAT."})

    def do_GET(self):
        self._send_json(405, {"error": "Use POST com JSON: { codes: [...] }"})

    def _send_json(self, status: int, payload: dict):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
