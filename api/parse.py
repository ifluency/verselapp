import io
import cgi
import zipfile
from http.server import BaseHTTPRequestHandler

import pandas as pd

from parser.parser import (
    process_pdf_bytes,
    build_memoria_calculo_text,
)


def _simple_text_pdf_bytes(text: str, title: str = "Memória de Cálculo") -> bytes:
    """
    Gera um PDF simples (texto monoespaçado) sem depender de bibliotecas externas.
    Observação: isso é um PDF mínimo, mas válido para abrir no Acrobat/Chrome.
    """
    # Sanitiza para latin-1 básico (pdf simples); substitui caracteres fora
    def to_pdf_safe(s: str) -> str:
        # Troca caracteres não latin-1 por '?'
        try:
            return s.encode("latin-1", "replace").decode("latin-1")
        except Exception:
            return s

    lines = [to_pdf_safe(l) for l in (text or "").splitlines()]
    # Limita linhas enormes (evita PDF gigante/timeout)
    if len(lines) > 20000:
        lines = lines[:20000] + ["", "[TRUNCADO: muitas linhas no relatório]"]

    # PDF básico com fonte Courier
    # Vamos paginar a cada ~55 linhas
    page_lines = 55
    pages = [lines[i:i + page_lines] for i in range(0, len(lines), page_lines)]
    if not pages:
        pages = [["(vazio)"]]

    # Helpers PDF
    def pdf_escape(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    objects = []
    xref = []

    def add_obj(obj_str: str) -> int:
        objects.append(obj_str)
        return len(objects)

    # 1) Catalog, 2) Pages
    # Vamos criar Page objects depois
    # Fonte
    font_obj = add_obj("<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>")

    # Pages placeholder (vamos preencher kids depois)
    pages_obj_index = add_obj("<< /Type /Pages /Kids [] /Count 0 >>")

    catalog_obj_index = add_obj(f"<< /Type /Catalog /Pages {pages_obj_index} 0 R >>")

    page_obj_indices = []
    content_obj_indices = []

    for pidx, plines in enumerate(pages, start=1):
        # Conteúdo da página
        # Coordenadas: começa no topo (y=800) e desce 14 por linha
        y_start = 800
        line_h = 14
        x = 40

        content_lines = []
        content_lines.append("BT")
        content_lines.append(f"/F1 10 Tf")
        content_lines.append(f"1 0 0 1 {x} {y_start} Tm")

        # Título na primeira página
        if pidx == 1:
            content_lines.append(f"({pdf_escape(title)}) Tj")
            content_lines.append(f"0 -{line_h*2} Td")

        for line in plines:
            content_lines.append(f"({pdf_escape(line)}) Tj")
            content_lines.append(f"0 -{line_h} Td")

        content_lines.append("ET")

        stream = "\n".join(content_lines).encode("latin-1", "replace")
        content_obj = add_obj(
            f"<< /Length {len(stream)} >>\nstream\n{stream.decode('latin-1')}\nendstream"
        )
        content_obj_indices.append(content_obj)

        page_obj = add_obj(
            f"<< /Type /Page /Parent {pages_obj_index} 0 R "
            f"/MediaBox [0 0 595 842] "
            f"/Resources << /Font << /F1 {font_obj} 0 R >> >> "
            f"/Contents {content_obj} 0 R >>"
        )
        page_obj_indices.append(page_obj)

    # Atualiza Pages obj (Kids + Count)
    kids = " ".join([f"{i} 0 R" for i in page_obj_indices])
    objects[pages_obj_index - 1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_obj_indices)} >>"

    # Monta arquivo PDF
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")

    # xref offsets
    for i, obj in enumerate(objects, start=1):
        xref.append(out.tell())
        out.write(f"{i} 0 obj\n".encode("ascii"))
        out.write(obj.encode("latin-1", "replace"))
        out.write(b"\nendobj\n")

    xref_start = out.tell()
    out.write(f"xref\n0 {len(objects)+1}\n".encode("ascii"))
    out.write(b"0000000000 65535 f \n")
    for off in xref:
        out.write(f"{off:010d} 00000 n \n".encode("ascii"))

    out.write(b"trailer\n")
    out.write(f"<< /Size {len(objects)+1} /Root {catalog_obj_index} 0 R >>\n".encode("ascii"))
    out.write(b"startxref\n")
    out.write(f"{xref_start}\n".encode("ascii"))
    out.write(b"%%EOF")
    return out.getvalue()


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

            if df is None or df.empty:
                return self._send_text(200, "Nenhuma linha encontrada no PDF (ou formato não reconhecido).")

            # Excel
            excel_buf = io.BytesIO()
            df.to_excel(excel_buf, index=False)
            excel_bytes = excel_buf.getvalue()

            # Memória de cálculo (texto -> pdf)
            memoria_txt = build_memoria_calculo_text(df)
            memoria_pdf = _simple_text_pdf_bytes(memoria_txt, title="Memória de Cálculo")

            # ZIP com os dois
            zip_buf = io.BytesIO()
            with zipfile.ZipFile(zip_buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
                z.writestr("relatorio.xlsx", excel_bytes)
                z.writestr("Memoria_de_Calculo.pdf", memoria_pdf)

            zip_bytes = zip_buf.getvalue()

            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", 'attachment; filename="resultado_extracao.zip"')
            self.send_header("Content-Length", str(len(zip_bytes)))
            self.end_headers()
            self.wfile.write(zip_bytes)

        except Exception as e:
            self._send_text(500, f"Erro ao processar: {str(e)}")

    def do_GET(self):
        self._send_text(405, "Use POST com multipart/form-data (campo 'file').")

    def _send_text(self, status: int, msg: str):
        data = (msg or "").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
