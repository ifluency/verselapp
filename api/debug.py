import io
import cgi
from http.server import BaseHTTPRequestHandler

from parser.parser import process_pdf_bytes, PdfIncompatibilityError


def preco_txt_to_float(preco_txt: str):
    if preco_txt is None:
        return None
    s = str(preco_txt).strip().replace("R$", "").strip()
    if not s:
        return None
    # PT-BR: 9.309,0000 -> 9309.0000
    s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except:
        return None


def coef_var(vals):
    if not vals:
        return None
    mean = sum(vals) / len(vals)
    if mean == 0:
        return None
    var = sum((v - mean) ** 2 for v in vals) / len(vals)
    std = var ** 0.5
    return std / mean


def media_sem_o_valor(vals, idx):
    if len(vals) <= 1:
        return None
    return (sum(vals) - vals[idx]) / (len(vals) - 1)


def audit_item(vals, upper=1.25, lower=0.75):
    altos = []
    keep_alto = []
    for i, v in enumerate(vals):
        m = media_sem_o_valor(vals, i)
        ratio = (v / m) if (m not in (None, 0)) else None
        if ratio is not None and ratio > upper:
            altos.append({"v": v, "m_outros": m, "ratio": ratio})
        else:
            keep_alto.append(v)

    baixos = []
    keep_baixo = []
    for i, v in enumerate(keep_alto):
        m = media_sem_o_valor(keep_alto, i)
        ratio = (v / m) if (m not in (None, 0)) else None
        if ratio is not None and ratio < lower:
            baixos.append({"v": v, "m_outros": m, "ratio": ratio})
        else:
            keep_baixo.append(v)

    final = keep_baixo[:]
    return {
        "iniciais": vals,
        "excluidos_altos": altos,
        "apos_alto": keep_alto,
        "excluidos_baixos": baixos,
        "finais": final,
        "media_final": (sum(final) / len(final)) if final else None,
        "cv_final": coef_var(final) if final else None,
    }


def build_audit_txt(df, max_items=5, min_n=5):
    """
    Debug dos cálculos:
    - Seleciona os primeiros 'max_items' itens cuja contagem BRUTA (linhas no DF) seja >= min_n
    - Mostra também quantos preços realmente viraram número (parse ok).
    """
    if df is None or getattr(df, "empty", True):
        return "DF vazio. Nenhuma linha encontrada.\n"

    required = {"Item", "Preço unitário"}
    missing = [c for c in required if c not in df.columns]
    if missing:
        return f"Colunas esperadas ausentes: {missing}. Colunas encontradas: {list(df.columns)}\n"

    out = []
    out.append("DEBUG — AUDITORIA DOS CÁLCULOS")
    out.append(f"Critério: itens com N BRUTO >= {min_n} (antes de parsing e antes de excluir alto/baixo)")
    out.append(f"Mostrando os {max_items} primeiros itens que atendem o critério.")
    out.append("Regras:")
    out.append(" - Excessivamente Elevados: v / média_outros > 1.25")
    out.append(" - Inexequíveis: v / média_outros < 0.75")
    out.append("")

    count = 0

    # Importante: agrupa no DF original (N BRUTO)
    for item, g_raw in df.groupby("Item", sort=False):
        n_bruto = len(g_raw)

        # Critério baseado no N BRUTO (não depende do N final)
        if n_bruto < min_n:
            continue

        # Agora faz parsing do preço só para o cálculo
        g = g_raw.copy()
        g["preco_num"] = g["Preço unitário"].apply(preco_txt_to_float)
        vals = g["preco_num"].dropna().astype(float).tolist()
        n_parse = len(vals)

        out.append("=" * 100)
        out.append(f"{item} | N bruto = {n_bruto} | N preços parseados = {n_parse}")

        if n_parse < 2:
            out.append("⚠️ Poucos preços parseados para auditoria (precisa de pelo menos 2).")
            out.append("Valores originais da coluna 'Preço unitário':")
            out.append(", ".join([str(x) for x in g_raw["Preço unitário"].tolist()[:50]]))
            out.append("")
            count += 1
            if count >= max_items:
                break
            continue

        rep = audit_item(vals, upper=1.25, lower=0.75)

        out.append("Valores (iniciais parseados):")
        out.append(", ".join([f"{v:.4f}" for v in rep["iniciais"]]))
        out.append("")

        out.append("--- Exclusões: Excessivamente Elevados (v / média_outros > 1.25) ---")
        out.append(f"Qtde: {len(rep['excluidos_altos'])}")
        for r in rep["excluidos_altos"]:
            out.append(f"v={r['v']:.4f} | media_outros={r['m_outros']:.4f} | ratio={r['ratio']:.4f}")
        out.append("")

        out.append("Após ALTO (mantidos):")
        out.append(", ".join([f"{v:.4f}" for v in rep["apos_alto"]]))
        out.append("")

        out.append("--- Exclusões: Inexequíveis (v / média_outros < 0.75) ---")
        out.append(f"Qtde: {len(rep['excluidos_baixos'])}")
        for r in rep["excluidos_baixos"]:
            out.append(f"v={r['v']:.4f} | media_outros={r['m_outros']:.4f} | ratio={r['ratio']:.4f}")
        out.append("")

        out.append("Finais:")
        out.append(", ".join([f"{v:.4f}" for v in rep["finais"]]))
        out.append(f"N final: {len(rep['finais'])}")
        media_txt = "" if rep["media_final"] is None else f"{rep['media_final']:.4f}"
        out.append(f"Média final: {media_txt}")
        cv_txt = "" if rep["cv_final"] is None else f"{rep['cv_final']:.6f}"
        out.append(f"CV final: {cv_txt}")
        out.append("")

        count += 1
        if count >= max_items:
            break

    if count == 0:
        out.append(f"Nenhum item com N BRUTO >= {min_n} encontrado no DF.")

    return "\n".join(out) + "\n"


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            content_type = self.headers.get("content-type", "")
            if "multipart/form-data" not in content_type:
                return self._send_text(400, "Envie multipart/form-data com campo 'file'.")

            content_length = int(self.headers.get("content-length", "0"))
            if content_length <= 0:
                return self._send_text(400, "Corpo vazio.")

            body = self.rfile.read(content_length)

            environ = {
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": content_type,
                "CONTENT_LENGTH": str(content_length),
            }
            fp = io.BytesIO(body)
            form = cgi.FieldStorage(fp=fp, environ=environ, keep_blank_values=True)

            if "file" not in form:
                return self._send_text(400, "Campo 'file' ausente.")

            pdf_bytes = form["file"].file.read()

            df = process_pdf_bytes(pdf_bytes)

            # Mostra 5 primeiros itens com N BRUTO >= 5
            txt = build_audit_txt(df, max_items=5, min_n=5)

            data = txt.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="debug_audit.txt"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        except PdfIncompatibilityError as e:
            self._send_text(400, str(e))

        except Exception as e:
            self._send_text(500, f"Erro no debug: {str(e)}")

    def do_GET(self):
        self._send_text(405, "Use POST com multipart/form-data (campo 'file').")

    def _send_text(self, status: int, msg: str):
        data = (msg or "").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
