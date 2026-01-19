import io
import cgi
import traceback
from urllib.parse import urlparse, parse_qs
from http.server import BaseHTTPRequestHandler

import pandas as pd
from parser.parser import process_pdf_bytes


def preco_txt_to_float(preco_txt: str) -> float | None:
    if preco_txt is None:
        return None
    s = str(preco_txt).strip()
    if not s:
        return None
    s = s.replace("R$", "").strip()
    s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except Exception:
        return None


def float_to_preco_txt(x: float | None) -> str:
    if x is None:
        return ""
    s = f"{x:.4f}"
    return s.replace(".", ",")


def coeficiente_variacao(vals: list[float]) -> float | None:
    if not vals:
        return None
    mean = sum(vals) / len(vals)
    if mean == 0:
        return None
    var = sum((v - mean) ** 2 for v in vals) / len(vals)  # ddof=0
    std = var ** 0.5
    return std / mean


def media_sem_o_valor(vals: list[float], idx: int) -> float | None:
    if len(vals) <= 1:
        return None
    s = sum(vals) - vals[idx]
    return s / (len(vals) - 1)


def filtrar_outliers_por_ratio(vals: list[float], upper: float = 1.25, lower: float = 0.75) -> list[float]:
    if len(vals) < 2:
        return vals[:]

    # PASSO 1/2 (elevados): ratio = valor / média(outros)
    keep = []
    for i, v in enumerate(vals):
        m = media_sem_o_valor(vals, i)
        if m is None or m == 0:
            keep.append(v)
            continue
        ratio = v / m
        if ratio <= upper:
            keep.append(v)

    # PASSO 3/4 (inexequíveis) no conjunto filtrado
    if len(keep) < 2:
        return keep

    keep2 = []
    for i, v in enumerate(keep):
        m = media_sem_o_valor(keep, i)
        if m is None or m == 0:
            keep2.append(v)
            continue
        ratio = v / m
        if ratio >= lower:
            keep2.append(v)

    return keep2


def gerar_resumo(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(
            columns=[
                "Item",
                "CATMAT",
                "Número de entradas iniciais",
                "Número de entradas finais",
                "Coeficiente de variação",
                "Preço Final escolhido",
                "Valor escolhido",
            ]
        )

    df_calc = df.copy()

    if "Preço unitário" not in df_calc.columns:
        # evita falha silenciosa
        raise ValueError("Coluna 'Preço unitário' não encontrada no dataframe.")

    df_calc["preco_num"] = df_calc["Preço unitário"].apply(preco_txt_to_float)
    df_calc = df_calc[df_calc["preco_num"].notna()].copy()

    rows = []

    for item, g in df_calc.groupby("Item", sort=False):
        catmat = g["CATMAT"].dropna().iloc[0] if g["CATMAT"].notna().any() else ""
        vals = g["preco_num"].astype(float).tolist()

        n_inicial = len(vals)

        # regra: <5 -> CV decide média/mediana; >=5 -> filtro e média
        if n_inicial < 5:
            cv = coeficiente_variacao(vals)
            mean = sum(vals) / len(vals) if vals else None
            med = float(pd.Series(vals).median()) if vals else None

            if cv is None:
                escolhido = "mediana"
                valor = med
            else:
                if cv < 0.25:
                    escolhido = "média"
                    valor = mean
                else:
                    escolhido = "mediana"
                    valor = med

            n_final = n_inicial
            cv_final = cv

        else:
            vals_filtrados = filtrar_outliers_por_ratio(vals, upper=1.25, lower=0.75)
            n_final = len(vals_filtrados)
            valor = (sum(vals_filtrados) / n_final) if n_final > 0 else None
            escolhido = "média"
            cv_final = coeficiente_variacao(vals_filtrados) if n_final > 0 else None

        rows.append(
            {
                "Item": item,
                "CATMAT": catmat,
                "Número de entradas iniciais": n_inicial,
                "Número de entradas finais": n_final,
                "Coeficiente de variação": (round(cv_final, 6) if cv_final is not None else ""),
                "Preço Final escolhido": escolhido,
                "Valor escolhido": float_to_preco_txt(valor),
            }
        )

    return pd.DataFrame(rows)


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        # debug via querystring ?debug=1
        q = parse_qs(urlparse(self.path).query)
        debug_mode = q.get("debug", ["0"])[0] in ("1", "true", "True", "yes", "sim")

        try:
            content_type = self.headers.get("content-type", "")
            if "multipart/form-data" not in content_type:
                self._send_text(400, "Envie multipart/form-data com campo 'file'.")
                return

            content_length = int(self.headers.get("content-length", "0"))
            if content_length <= 0:
                self._send_text(400, "Corpo vazio.")
                return

            body = self.rfile.read(content_length)

            environ = {
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": content_type,
                "CONTENT_LENGTH": str(content_length),
            }
            fp = io.BytesIO(body)
            form = cgi.FieldStorage(fp=fp, environ=environ, keep_blank_values=True)

            if "file" not in form:
                self._send_text(400, "Campo 'file' ausente.")
                return

            pdf_bytes = form["file"].file.read()

            df = process_pdf_bytes(pdf_bytes)
            if df is None or df.empty:
                self._send_text(200, "Nenhuma linha com Compõe=Sim foi encontrada no arquivo enviado.")
                return

            df_resumo = gerar_resumo(df)

            out = io.BytesIO()
            with pd.ExcelWriter(out, engine="openpyxl") as writer:
                df.to_excel(writer, index=False, sheet_name="Dados")
                df_resumo.to_excel(writer, index=False, sheet_name="Resumo")

            out.seek(0)
            xlsx_bytes = out.read()

            filename = "relatorio_precos_compoe_sim.xlsx"
            self.send_response(200)
            self.send_header(
                "Content-Type",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(len(xlsx_bytes)))
            self.end_headers()
            self.wfile.write(xlsx_bytes)

        except Exception as e:
            tb = traceback.format_exc()

            # Console do Vercel (Logs)
            print("ERROR /api/parse:", str(e))
            print(tb)

            if debug_mode:
                # Devolve o stacktrace pro navegador
                self._send_text(500, f"Erro ao processar:\n{str(e)}\n\nSTACKTRACE:\n{tb}")
            else:
                # Mensagem curta pro usuário final
                self._send_text(500, "Falha ao processar. Tente novamente ou use /api/parse?debug=1 para ver detalhes.")

    def do_GET(self):
        self._send_text(405, "Use POST com multipart/form-data (campo 'file').")

    def _send_text(self, status: int, msg: str):
        data = (msg or "").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
