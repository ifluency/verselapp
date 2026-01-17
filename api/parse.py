import io
import cgi
from http.server import BaseHTTPRequestHandler

import pandas as pd
from parser.parser import process_pdf_bytes


def preco_txt_to_float(preco_txt: str) -> float | None:
    """
    Converte texto pt-br para float para cálculo.
    Ex:
      "9.309,0000" -> 9309.0
      "150,4500" -> 150.45
    """
    if preco_txt is None:
        return None
    s = str(preco_txt).strip()
    if not s:
        return None
    # remove milhar e troca decimal
    s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except Exception:
        return None


def float_to_preco_txt(x: float | None) -> str:
    """
    Volta para texto com vírgula e 4 casas, para o Excel pt-br ficar consistente.
    """
    if x is None:
        return ""
    s = f"{x:.4f}"
    return s.replace(".", ",")


def coeficiente_variacao(vals: list[float]) -> float | None:
    """
    CV = desvio padrão / média
    Usa desvio padrão populacional (ddof=0) para estabilidade.
    """
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
    """
    Aplica os passos 1-4 do seu método para n>=5:
    - ratio = valor / média(dos outros)
    - remove "elevados" (ratio > upper)
    - recalcula ratios no conjunto restante
    - remove "inexequíveis" (ratio < lower)
    Retorna lista final (mantém ordem original dos remanescentes).
    """
    if len(vals) < 2:
        return vals[:]

    # PASSO 1/2: remove elevados (ratio > upper)
    keep = []
    for i, v in enumerate(vals):
        m = media_sem_o_valor(vals, i)
        if m is None or m == 0:
            keep.append(v)
            continue
        ratio = v / m
        if ratio <= upper:
            keep.append(v)

    # PASSO 3/4: remove inexequíveis (ratio < lower) com base no conjunto já filtrado
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
    """
    Gera um DF com 1 linha por Item:
    Item; CATMAT; Entradas iniciais; Entradas finais; CV; Preço final escolhido; Valor escolhido
    """
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

    # converte preço para float (apenas para cálculo)
    df_calc = df.copy()
    df_calc["preco_num"] = df_calc["Preço unitário"].apply(preco_txt_to_float)

    # remove linhas sem preço numérico
    df_calc = df_calc[df_calc["preco_num"].notna()].copy()

    rows = []

    # agrupa por Item
    for item, g in df_calc.groupby("Item", sort=False):
        catmat = g["CATMAT"].dropna().iloc[0] if g["CATMAT"].notna().any() else ""
        vals = g["preco_num"].astype(float).tolist()

        n_inicial = len(vals)

        # Se quiser "mais de 5" estritamente, troque para: if n_inicial <= 5: ...
        if n_inicial < 5:
            cv = coeficiente_variacao(vals)
            mean = sum(vals) / len(vals) if vals else None
            med = float(pd.Series(vals).median()) if vals else None

            if cv is None:
                # fallback: mediana
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
            # método outliers
            vals_filtrados = filtrar_outliers_por_ratio(vals, upper=1.25, lower=0.75)
            n_final = len(vals_filtrados)

            # média final sempre
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
