import io
import cgi
import traceback
from urllib.parse import urlparse, parse_qs
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
    s = s.replace("R$", "").strip()
    s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
@@ -27,20 +23,13 @@


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
@@ -59,18 +48,10 @@


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
    # PASSO 1/2 (elevados): ratio = valor / média(outros)
    keep = []
    for i, v in enumerate(vals):
        m = media_sem_o_valor(vals, i)
@@ -81,7 +62,7 @@
        if ratio <= upper:
            keep.append(v)

    # PASSO 3/4: remove inexequíveis (ratio < lower) com base no conjunto já filtrado
    # PASSO 3/4 (inexequíveis) no conjunto filtrado
    if len(keep) < 2:
        return keep

@@ -99,10 +80,6 @@


def gerar_resumo(df: pd.DataFrame) -> pd.DataFrame:
    """
    Gera um DF com 1 linha por Item:
    Item; CATMAT; Entradas iniciais; Entradas finais; CV; Preço final escolhido; Valor escolhido
    """
    if df is None or df.empty:
        return pd.DataFrame(
            columns=[
@@ -116,30 +93,30 @@
            ]
        )

    # converte preço para float (apenas para cálculo)
    df_calc = df.copy()
    df_calc["preco_num"] = df_calc["Preço unitário"].apply(preco_txt_to_float)

    # remove linhas sem preço numérico
    if "Preço unitário" not in df_calc.columns:
        # evita falha silenciosa
        raise ValueError("Coluna 'Preço unitário' não encontrada no dataframe.")

    df_calc["preco_num"] = df_calc["Preço unitário"].apply(preco_txt_to_float)
    df_calc = df_calc[df_calc["preco_num"].notna()].copy()

    rows = []

    # agrupa por Item
    for item, g in df_calc.groupby("Item", sort=False):
        catmat = g["CATMAT"].dropna().iloc[0] if g["CATMAT"].notna().any() else ""
        vals = g["preco_num"].astype(float).tolist()

        n_inicial = len(vals)

        # Se quiser "mais de 5" estritamente, troque para: if n_inicial <= 5: ...
        # regra: <5 -> CV decide média/mediana; >=5 -> filtro e média
        if n_inicial < 5:
            cv = coeficiente_variacao(vals)
            mean = sum(vals) / len(vals) if vals else None
            med = float(pd.Series(vals).median()) if vals else None

            if cv is None:
                # fallback: mediana
                escolhido = "mediana"
                valor = med
            else:
@@ -154,14 +131,10 @@
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
@@ -181,6 +154,10 @@

class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        # debug via querystring ?debug=1
        q = parse_qs(urlparse(self.path).query)
        debug_mode = q.get("debug", ["0"])[0] in ("1", "true", "True", "yes", "sim")

        try:
            content_type = self.headers.get("content-type", "")
            if "multipart/form-data" not in content_type:
@@ -209,7 +186,6 @@
            pdf_bytes = form["file"].file.read()

            df = process_pdf_bytes(pdf_bytes)

            if df is None or df.empty:
                self._send_text(200, "Nenhuma linha com Compõe=Sim foi encontrada no arquivo enviado.")
                return
@@ -236,15 +212,26 @@
            self.wfile.write(xlsx_bytes)

        except Exception as e:
            self._send_text(500, f"Erro ao processar: {str(e)}")
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
