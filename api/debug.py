from parser import process_pdf_bytes_debug, debug_dump, validate_extraction

def _read_pdf_from_multipart(req):
    """
    Vercel Python: normalmente req.files funciona em algumas libs/frameworks.
    Como cada setup varia, deixei fallback pra bytes diretos também.
    """
    # 1) Se seu runtime fornece req.files
    if hasattr(req, "files") and req.files and "file" in req.files:
        f = req.files["file"]
        return f.read()

    # 2) Se vier como body puro (application/pdf)
    if hasattr(req, "body") and req.body:
        return req.body

    # 3) Se vier como data (algumas versões)
    if hasattr(req, "get_data"):
        return req.get_data()

    raise ValueError("PDF não encontrado. Envie via multipart (campo 'file') ou body bruto.")


def handler(req):
    if getattr(req, "method", "GET") != "POST":
        return {
            "statusCode": 405,
            "headers": {"content-type": "application/json; charset=utf-8"},
            "body": '{"error":"Use POST com multipart: file=<pdf>"}',
        }

    try:
        pdf_bytes = _read_pdf_from_multipart(req)
        df, debug_records = process_pdf_bytes_debug(pdf_bytes)

        report = {
            "validation": validate_extraction(df),
            "rows": int(len(df)),
        }
        dump_txt = debug_dump(df, debug_records, max_rows=80)

        # devolve JSON com o dump (pra UI mostrar bonitinho)
        import json
        body = json.dumps(
            {
                "report": report,
                "debug_dump": dump_txt,
            },
            ensure_ascii=False
        )

        return {
            "statusCode": 200,
            "headers": {"content-type": "application/json; charset=utf-8"},
            "body": body,
        }

    except Exception as e:
        import json
        return {
            "statusCode": 500,
            "headers": {"content-type": "application/json; charset=utf-8"},
            "body": json.dumps({"error": str(e)}, ensure_ascii=False),
        }
