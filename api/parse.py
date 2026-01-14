import io
import cgi
import pandas as pd
from parser.parser import process_pdf_bytes

def handler(request):
    # Parse multipart/form-data
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" not in content_type:
        return (400, {"content-type": "text/plain"}, b"Envie um multipart/form-data com o arquivo.")

    environ = {
        "REQUEST_METHOD": "POST",
        "CONTENT_TYPE": content_type,
        "CONTENT_LENGTH": request.headers.get("content-length", "0"),
    }
    fp = io.BytesIO(request.body)
    form = cgi.FieldStorage(fp=fp, environ=environ, keep_blank_values=True)

    if "file" not in form:
        return (400, {"content-type": "text/plain"}, b"Campo 'file' ausente.")

    file_item = form["file"]
    pdf_bytes = file_item.file.read()

    df = process_pdf_bytes(pdf_bytes)

    # Se vazio, devolve CSV informativo (ou 204)
    if df is None or df.empty:
        return (200, {"content-type": "text/plain; charset=utf-8"}, "Sem linhas Compõe=Sim.".encode("utf-8"))

    out = io.BytesIO()
    df.to_excel(out, index=False)
    out.seek(0)
    xlsx_bytes = out.read()

    headers = {
        "content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "content-disposition": 'attachment; filename="saida_compoe_sim.xlsx"',
    }
    return (200, headers, xlsx_bytes)
