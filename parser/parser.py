import re
import io
import json
import os
import base64
from datetime import datetime

try:
    # Python 3.9+
    from zoneinfo import ZoneInfo  # type: ignore
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore
import pdfplumber
import pandas as pd

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    SimpleDocTemplate,
    Table,
    TableStyle,
    Paragraph,
    Spacer,
    Image,
    PageBreak,
    KeepTogether,
)


# ===============================
# Validação do tipo de relatório
# ===============================


class PdfIncompatibilityError(Exception):
    """Erro amigável para indicar que o PDF enviado não é compatível."""


def _validate_relatorio_resumido_or_raise(pdf: pdfplumber.PDF):
    """Valida se o PDF é o relatório correto (Resumido).

    Regras:
      - Se na primeira página existir "Relatório Resumido" -> OK
      - Se existir "Relatório Detalhado" -> erro (usuário enviou o PDF errado)
      - Se não existir nenhum dos dois -> erro de incompatibilidade
    """
    if not getattr(pdf, "pages", None) or len(pdf.pages) == 0:
        raise PdfIncompatibilityError("PDF inválido: não foi possível ler páginas do arquivo.")

    first_text = (pdf.pages[0].extract_text(layout=True) or "").lower()

    has_resumido = ("relatório resumido" in first_text) or ("relatorio resumido" in first_text)
    has_detalhado = ("relatório detalhado" in first_text) or ("relatorio detalhado" in first_text)

    if has_resumido:
        return

    if has_detalhado:
        raise PdfIncompatibilityError(
            "PDF incorreto: você carregou o Relatório Detalhado. "
            "Por favor, utilize a versão Relatório Resumido."
        )

    raise PdfIncompatibilityError(
        "PDF incompatível: não foi possível identificar 'Relatório Resumido' nem 'Relatório Detalhado' "
        "no início do documento. Verifique se o arquivo enviado é o relatório resumido do ComprasGOV."
    )

RE_ITEM = re.compile(r"^Item\s*:?\s*(\d+)\b", re.IGNORECASE)
RE_CATMAT = re.compile(r"(\d{6})\s*-\s*")

RE_PAGE_MARK = re.compile(r"^\s*\d+\s+de\s+\d+\s*$", re.IGNORECASE)
RE_DATE_TOKEN = re.compile(r"^\d{2}/\d{2}/\d{4}$")
RE_ROW_START = re.compile(r"^\s*(\d+)\s+([IVX]+)\b", re.IGNORECASE)

INCISO_TO_FONTE = {
    "I": "Compras.gov.br",
    "II": "Contratações similares",
    "III": "Mídias Especializadas",
    "IV": "Fornecedor",
    "V": "Nota Fiscal Eletrônicas",
}

FINAL_COLUMNS = [
    "Item",
    "CATMAT",
    "Nº",
    "Inciso",
    "Fonte",
    "Quantidade",
    "Preço unitário",
    "Data",
    "Compõe",
]


def clean_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def normalize_text(s: str) -> str:
    s = (s or "").replace("\u00a0", " ")
    s = clean_spaces(s)

    # gov. br -> gov.br
    s = re.sub(r"(gov\.)\s*(br)\b", r"\1\2", s, flags=re.IGNORECASE)
    # Compras.gov. br -> Compras.gov.br
    s = re.sub(r"(Compras\.gov\.)\s*(br)\b", r"\1\2", s, flags=re.IGNORECASE)

    # “110Unidade” -> “110 Unidade”
    s = re.sub(r"(\d)([A-Za-zÀ-ÿ])", r"\1 \2", s)
    s = re.sub(r"([A-Za-zÀ-ÿ])(\d)", r"\1 \2", s)

    # R$ com espaço
    s = re.sub(r"R\$\s+", "R$ ", s)
    return s


def is_table_on(line: str) -> bool:
    """Detecta o início da tabela.

    O PDF pode variar: às vezes existe 'Período:', às vezes só o cabeçalho 'Nº Inciso Nome Quantidade ...'.
    """
    s = normalize_text(line).lower()
    if ("período:" in s) or ("periodo:" in s):
        return True
    # Cabeçalho típico
    if ("nº" in s or "no" in s) and ("inciso" in s) and ("quantidade" in s):
        return True
    return False


def is_table_off(line: str) -> bool:
    s = normalize_text(line).lower()
    return s.startswith("legenda")


def is_header(line: str) -> bool:
    s = normalize_text(line).lower()
    return s.startswith("nº inciso nome quantidade")


def parse_row_fields(row_line: str):
    """Parseia a linha do registro (pode conter coluna Nome).

    Exemplo comum:
      '4 I 110 Unidade R$ 150,4500 05/12/2025 Sim'

    Estratégia mais robusta (de trás pra frente):
      - Compõe: último token (aceita variações de SIM/NAO/Não)
      - Data: último token que parece dd/mm/aaaa
      - Preço: último padrão numérico antes da data (aceita 'R$' separado)
      - Quantidade: último padrão numérico antes do preço
    """
    s = normalize_text(row_line)
    toks = s.split()

    if len(toks) < 6:
        return None
    if not toks[0].isdigit():
        return None
    if not re.fullmatch(r"[IVX]+", toks[1], flags=re.IGNORECASE):
        return None

    no = toks[0]
    inciso = toks[1].upper()

    # Compõe (aceita Sim/Não/NAO/SIM com pontuação)
    comp_raw = re.sub(r"[^A-Za-zÀ-ÿ]+", "", toks[-1]).strip().lower()
    if comp_raw in ("sim",):
        compoe = "Sim"
    elif comp_raw in ("nao", "não", "non"):  # tolerância
        compoe = "Não"
    else:
        return None

    # Data
    date_idx = None
    for i in range(len(toks) - 1, -1, -1):
        if RE_DATE_TOKEN.fullmatch(toks[i]):
            date_idx = i
            break
    if date_idx is None:
        return None
    data = toks[date_idx]

    price_pat = re.compile(r"^\d{1,3}(?:\.\d{3})*,\d{2,4}$")
    # Quantidade pode vir sem separador de milhar (ex.: 1252, 4500) ou com (ex.: 1.252)
    qty_pat = re.compile(r"^\d+(?:\.\d{3})*(?:[\.,]\d+)?$")

    # Preço: procurar de trás pra frente antes da data
    preco_raw = None
    preco_idx = None
    for i in range(date_idx - 1, 1, -1):
        t = toks[i]
        if price_pat.fullmatch(t):
            preco_raw = t
            preco_idx = i
            break
        # caso 'R$' esteja separado
        if t in ("R$", "R$") and i + 1 < len(toks) and price_pat.fullmatch(toks[i + 1]):
            preco_raw = toks[i + 1]
            preco_idx = i
            break
        if t.startswith("R$") and price_pat.fullmatch(t.replace("R$", "").strip()):
            preco_raw = t.replace("R$", "").strip()
            preco_idx = i
            break
    if preco_raw is None:
        # fallback: procurar token numérico antes da data
        for i in range(date_idx - 1, 1, -1):
            if re.fullmatch(r"^\d+(?:\.\d{3})*(?:,\d+)?$", toks[i]):
                preco_raw = toks[i]
                preco_idx = i
                break
    if preco_raw is None or preco_idx is None:
        return None

    # Quantidade: normalmente é o PRIMEIRO número após Nº/Inciso (antes da unidade e do preço)
    qtd = None
    for j in range(2, preco_idx):
        if qty_pat.fullmatch(toks[j]):
            qtd = toks[j]
            break
    if qtd is None:
        return None

    return {
        "Nº": no,
        "Inciso": inciso,
        "Quantidade": qtd,
        "Preço unitário": preco_raw,
        "Data": data,
        "Compõe": compoe,
    }


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


def float_to_preco_txt(x: float | None, decimals: int = 2) -> str:
    if x is None:
        return ""
    s = f"{x:.{decimals}f}"
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


def filtrar_outliers_por_ratio(vals: list[float], upper: float = 1.25, lower: float = 0.75):
    """
    Retorna:
      - vals_final: lista final após filtros
      - excluidos_alto: quantos foram removidos por excessivamente elevados
      - excluidos_baixo: quantos foram removidos por inexequíveis
    Regras:
      - alto: remove se (v / média(outros)) > upper
      - baixo: após remover altos, remove se (v / média(outros)) < lower
    """
    if len(vals) < 2:
        return vals[:], 0, 0

    # PASSO alto
    keep_alto = []
    excl_alto = 0
    for i, v in enumerate(vals):
        m = media_sem_o_valor(vals, i)
        if m is None or m == 0:
            keep_alto.append(v)
            continue
        ratio = v / m
        if ratio <= upper:
            keep_alto.append(v)
        else:
            excl_alto += 1

    if len(keep_alto) < 2:
        return keep_alto, excl_alto, 0

    # PASSO baixo
    keep_baixo = []
    excl_baixo = 0
    for i, v in enumerate(keep_alto):
        m = media_sem_o_valor(keep_alto, i)
        if m is None or m == 0:
            keep_baixo.append(v)
            continue
        ratio = v / m
        if ratio >= lower:
            keep_baixo.append(v)
        else:
            excl_baixo += 1

    return keep_baixo, excl_alto, excl_baixo


def filtrar_outliers_por_ratio_com_indices(
    pares: list[tuple[int, float]],
    upper: float = 1.25,
    lower: float = 0.75,
):
    """Versão com índices (para UI/ajuste manual).

    Entrada: lista de (idx_original, valor)
    Retorna:
      - pares_finais (mantidos)
      - excluidos_altos_idx (excessivamente elevados)
      - excluidos_baixos_idx (inexequíveis)
    """
    if len(pares) < 2:
        return pares[:], [], []

    # PASSO alto
    keep_alto: list[tuple[int, float]] = []
    excl_altos: list[int] = []
    for i, (idx, v) in enumerate(pares):
        others = [vv for j, (_, vv) in enumerate(pares) if j != i]
        m = sum(others) / len(others) if others else None
        if m is None or m == 0:
            keep_alto.append((idx, v))
            continue
        ratio = v / m
        if ratio <= upper:
            keep_alto.append((idx, v))
        else:
            excl_altos.append(idx)

    if len(keep_alto) < 2:
        return keep_alto, excl_altos, []

    # PASSO baixo
    keep_baixo: list[tuple[int, float]] = []
    excl_baixos: list[int] = []
    for i, (idx, v) in enumerate(keep_alto):
        others = [vv for j, (_, vv) in enumerate(keep_alto) if j != i]
        m = sum(others) / len(others) if others else None
        if m is None or m == 0:
            keep_baixo.append((idx, v))
            continue
        ratio = v / m
        if ratio >= lower:
            keep_baixo.append((idx, v))
        else:
            excl_baixos.append(idx)

    return keep_baixo, excl_altos, excl_baixos



def _median(vals: list[float]) -> float | None:
    if not vals:
        return None
    return float(pd.Series(vals).median())


def _mean(vals: list[float]) -> float | None:
    if not vals:
        return None
    return sum(vals) / len(vals)


def _std_pop(vals: list[float]) -> float | None:
    if not vals:
        return None
    m = _mean(vals)
    if m is None:
        return None
    var = sum((v - m) ** 2 for v in vals) / len(vals)
    return var ** 0.5


def _cv(vals: list[float]) -> float | None:
    if not vals:
        return None
    m = _mean(vals)
    if m in (None, 0):
        return None
    s = _std_pop(vals)
    if s is None:
        return None
    return s / m


def _safe_float(x) -> float | None:
    try:
        if x is None:
            return None
        return float(x)
    except Exception:
        return None


def build_itens_relatorio(
    df: pd.DataFrame,
    payload: dict | None = None,
) -> list[dict]:
    """Constrói um relatório por item, usado no Preview, Excel e PDFs.

    payload (opcional) pode conter:
      - last_quotes: {"Item 1": 123.45, ...}
      - manual_overrides: {
            "Item 1": {
                "included_indices": [0,2,3],
                "method": "media"|"mediana",
                "justificativa_codigo": "...",
                "justificativa_texto": "..."
            }, ...
        }

    IMPORTANTE: included_indices são índices relativos à lista 'valores_brutos' retornada no preview.
    """
    payload = payload or {}
    last_quotes = payload.get("last_quotes") or {}
    manual_overrides = payload.get("manual_overrides") or {}

    if df is None or df.empty:
        return []

    if "Preço unitário" not in df.columns:
        raise ValueError("Coluna 'Preço unitário' não encontrada no dataframe.")

    df_calc = df.copy()
    df_calc["preco_num"] = df_calc["Preço unitário"].apply(preco_txt_to_float)

    itens: list[dict] = []

    for item, g_raw in df.groupby("Item", sort=False):
        catmat = g_raw["CATMAT"].dropna().iloc[0] if ("CATMAT" in g_raw.columns and g_raw["CATMAT"].notna().any()) else ""

        # valores brutos (numéricos) e fonte (alinhados) na ordem das linhas
        # Observação: índices do override manual se referem a essa lista numérica filtrada.
        valores_brutos: list[float] = []
        fontes_brutos: list[str] = []
        for _, row in g_raw.iterrows():
            fv = _safe_float(preco_txt_to_float(row.get("Preço unitário")))
            if fv is None:
                continue
            valores_brutos.append(float(fv))
            fontes_brutos.append(str(row.get("Fonte") or ""))

        n_bruto = int(len(g_raw))

        # --------- cálculo automático (base atual)
        excl_alto = 0
        excl_baixo = 0
        n_final = 0
        cv_final = None
        metodo_auto = ""
        valor_auto = None
        valores_finais_auto: list[float] = []

        if len(valores_brutos) == 0:
            # sem valores numéricos
            metodo_auto = ""
            valor_auto = None
            valores_finais_auto = []
            n_final = 0
            cv_final = None
        elif len(valores_brutos) == 1:
            metodo_auto = "Valor único"
            valor_auto = valores_brutos[0]
            valores_finais_auto = valores_brutos[:]
            n_final = 1
            cv_final = None
        elif len(valores_brutos) < 5:
            cv = _cv(valores_brutos)
            mean = _mean(valores_brutos)
            med = _median(valores_brutos)
            if cv is None:
                metodo_auto = "Mediana"
                valor_auto = med
            else:
                if cv < 0.25:
                    metodo_auto = "Média"
                    valor_auto = mean
                else:
                    metodo_auto = "Mediana"
                    valor_auto = med
            # Índices do cálculo automático (para UI/ajuste manual)
            auto_excl_altos_idx: list[int] = []
            auto_excl_baixos_idx: list[int] = []
            auto_keep_idx: list[int] = list(range(len(valores_brutos)))

            valores_finais_auto = valores_brutos[:]
            n_final = len(valores_finais_auto)
            cv_final = cv
        else:
            pares = [(i, v) for i, v in enumerate(valores_brutos)]
            keep_pairs, auto_excl_altos_idx, auto_excl_baixos_idx = filtrar_outliers_por_ratio_com_indices(
                pares, upper=1.25, lower=0.75
            )
            auto_keep_idx = [i for i, _ in keep_pairs]
            excl_alto = len(auto_excl_altos_idx)
            excl_baixo = len(auto_excl_baixos_idx)
            valores_finais_auto = [v for _, v in keep_pairs]
            n_final = len(valores_finais_auto)
            valor_auto = _mean(valores_finais_auto) if n_final > 0 else None
            metodo_auto = "Média"
            cv_final = _cv(valores_finais_auto) if n_final > 0 else None

        # --------- último licitado
        last_quote_val = last_quotes.get(item)
        last_quote = _safe_float(last_quote_val)

        # --------- decisão final (auto vs manual)
        modo = "Automático"
        valor_final = valor_auto
        metodo_final = metodo_auto
        valores_finais = valores_finais_auto[:]
        manual_info = None

        # Só aceita override manual quando o valor calculado estiver até 20% acima do último licitado
        # (i.e., valor_auto <= 1.2 * last_quote)
        allow_manual = (
            last_quote is not None
            and last_quote > 0
            and valor_auto is not None
            and valor_auto <= (1.2 * last_quote)
        )

        ov = manual_overrides.get(item) if isinstance(manual_overrides, dict) else None
        if allow_manual and isinstance(ov, dict):
            included_indices = ov.get("included_indices")
            method = (ov.get("method") or "media").lower()
            if isinstance(included_indices, list) and len(included_indices) > 0:
                # included_indices referem-se à lista de valores_brutos (numéricos) do preview.
                sel = []
                for idx in included_indices:
                    if isinstance(idx, int) and 0 <= idx < len(valores_brutos):
                        sel.append(valores_brutos[idx])
                if len(sel) > 0:
                    modo = "Manual"
                    valores_finais = sel
                    if method in ("mediana", "median"):
                        metodo_final = "Mediana"
                        valor_final = _median(sel)
                    else:
                        metodo_final = "Média"
                        valor_final = _mean(sel)

                    manual_info = {
                        "included_indices": included_indices,
                        "excluded_count": int(len(valores_brutos) - len(sel)),
                        "method": metodo_final,
                        "valor_final": valor_final,
                        "cv": _cv(sel),
                        "mean": _mean(sel),
                        "median": _median(sel),
                        "justificativa_codigo": ov.get("justificativa_codigo") or "",
                        "justificativa_texto": ov.get("justificativa_texto") or "",
                    }

        # comparação
        comparacao = ""
        diff_abs = None
        diff_pct = None
        if last_quote is not None and valor_final is not None:
            if valor_final > last_quote:
                comparacao = "Maior"
            elif valor_final < last_quote:
                comparacao = "Menor"
            else:
                comparacao = "Igual"
            diff_abs = valor_final - last_quote
            diff_pct = (diff_abs / last_quote) if last_quote not in (None, 0) else None

        itens.append(
            {
                "item": item,
                "catmat": catmat,
                "n_bruto": n_bruto,
                "n_brutos_numericos": int(len(valores_brutos)),
                "valores_brutos": valores_brutos,
                "fontes_brutos": fontes_brutos,
                "auto_keep_idx": auto_keep_idx,
                "auto_excl_altos_idx": auto_excl_altos_idx,
                "auto_excl_baixos_idx": auto_excl_baixos_idx,
                "valor_auto": valor_auto,
                "metodo_auto": metodo_auto,
                "n_final_auto": int(len(valores_finais_auto)),
                "n_final_final": int(len(valores_finais)),
                "excl_altos": int(excl_alto),
                "excl_baixos": int(excl_baixo),
                "cv_auto": cv_final,
                "valores_finais_auto": valores_finais_auto,
                "allow_manual": bool(allow_manual),
                "modo_final": modo,
                "metodo_final": metodo_final,
                "valor_final": valor_final,
                "valores_finais": valores_finais,
                "cv_final": _cv(valores_finais) if valores_finais else None,
                "last_quote": last_quote,
                "comparacao": comparacao,
                "diff_abs": diff_abs,
                "diff_pct": diff_pct,
                "manual": manual_info,
            }
        )

    return itens


def gerar_resumo(df: pd.DataFrame) -> pd.DataFrame:
    cols = [
        "Item",
        "CATMAT",
        "Número de entradas iniciais",
        "Número de entradas finais",
        "Excessivamente Elevados",
        "Inexequíveis",
        "Coeficiente de variação",
        "Preço Final escolhido",
        "Valor escolhido",
    ]

    if df is None or df.empty:
        return pd.DataFrame(columns=cols)

    if "Preço unitário" not in df.columns:
        raise ValueError("Coluna 'Preço unitário' não encontrada no dataframe.")

    df_calc = df.copy()
    df_calc["preco_num"] = df_calc["Preço unitário"].apply(preco_txt_to_float)
    df_calc = df_calc[df_calc["preco_num"].notna()].copy()

    rows = []
    for item, g in df_calc.groupby("Item", sort=False):
        catmat = g["CATMAT"].dropna().iloc[0] if g["CATMAT"].notna().any() else ""
        vals = g["preco_num"].astype(float).tolist()
        n_inicial = len(vals)

        excl_alto = 0
        excl_baixo = 0

        if n_inicial < 5:
            cv = coeficiente_variacao(vals)
            mean = sum(vals) / len(vals) if vals else None
            med = float(pd.Series(vals).median()) if vals else None

            if cv is None:
                escolhido = "Mediana"
                valor = med
            else:
                if cv < 0.25:
                    escolhido = "Média"
                    valor = mean
                else:
                    escolhido = "Mediana"
                    valor = med

            n_final = n_inicial
            cv_final = cv

        else:
            vals_filtrados, excl_alto, excl_baixo = filtrar_outliers_por_ratio(vals, upper=1.25, lower=0.75)
            n_final = len(vals_filtrados)
            valor = (sum(vals_filtrados) / n_final) if n_final > 0 else None
            escolhido = "Média"
            cv_final = coeficiente_variacao(vals_filtrados) if n_final > 0 else None

        rows.append(
            {
                "Item": item,
                "CATMAT": catmat,
                "Número de entradas iniciais": n_inicial,
                "Número de entradas finais": n_final,
                "Excessivamente Elevados": excl_alto,
                "Inexequíveis": excl_baixo,
                "Coeficiente de variação": (round(cv_final, 6) if cv_final is not None else ""),
                "Preço Final escolhido": escolhido,
                "Valor escolhido": float_to_preco_txt(valor, decimals=2),
            }
        )

    return pd.DataFrame(rows, columns=cols)


def build_excel_bytes(df: pd.DataFrame, itens_relatorio: list[dict]) -> bytes:
    """Gera Excel (bytes) com:
    - Dados (linhas Compõe=Sim)
    - Resumo (cálculo automático atual)
    - Prévia (tabela comparativa + último licitado + modo final)
    """
    df_resumo = gerar_resumo(df)

    preview_rows = []
    for it in itens_relatorio:
        preview_rows.append(
            {
                "Item": it.get("item"),
                "Catmat": it.get("catmat"),
                "Número de entradas iniciais": it.get("n_bruto"),
                "Número de entradas finais": it.get("n_final_final") or it.get("n_final_auto"),
                "Nº desconsiderados (Excessivamente Elevados)": it.get("excl_altos"),
                "Nº desconsiderados (Inexequíveis)": it.get("excl_baixos"),
                "Valor calculado (R$)": float_to_preco_txt(_safe_float(it.get("valor_auto")), decimals=2),
                "Último licitado (R$)": float_to_preco_txt(_safe_float(it.get("last_quote")), decimals=2),
                "Modo final": it.get("modo_final"),
                "Método final": it.get("metodo_final"),
                "Valor final adotado (R$)": float_to_preco_txt(_safe_float(it.get("valor_final")), decimals=2),
                "Diferença vs último (R$)": float_to_preco_txt(
                    (_safe_float(it.get("valor_final")) - _safe_float(it.get("last_quote")))
                    if (_safe_float(it.get("valor_final")) is not None and _safe_float(it.get("last_quote")) is not None)
                    else None,
                    decimals=2,
                ),
                "Diferença vs último (%)": (
                    f"{(((_safe_float(it.get('valor_final')) - _safe_float(it.get('last_quote'))) / _safe_float(it.get('last_quote'))) * 100.0):.2f}%".replace(".", ",")
                    if (_safe_float(it.get("valor_final")) is not None and _safe_float(it.get("last_quote")) not in (None, 0))
                    else ""
                ),
            }
        )

    df_preview = pd.DataFrame(preview_rows)

    # IMPORTANTE:
    # Não use `df or ...` com DataFrame, pois o pandas não permite avaliar DataFrame
    # como booleano ("truth value is ambiguous"). Isso quebrava o /api/generate.
    df_to_write = df if df is not None else pd.DataFrame()

    excel_out = io.BytesIO()
    with pd.ExcelWriter(excel_out, engine="openpyxl") as writer:
        df_to_write.to_excel(writer, index=False, sheet_name="Dados")
        df_resumo.to_excel(writer, index=False, sheet_name="Resumo")
        df_preview.to_excel(writer, index=False, sheet_name="Prévia")

    excel_out.seek(0)
    return excel_out.read()


def process_pdf_bytes_debug(pdf_bytes: bytes) -> tuple[pd.DataFrame, list[dict]]:
    records: list[dict] = []
    debug_records: list[dict] = []

    current_item = None
    current_catmat = None
    capture = False

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        # Valida rapidamente se o PDF é o relatório correto (Resumido)
        _validate_relatorio_resumido_or_raise(pdf)
        for page in pdf.pages:
            text = page.extract_text(layout=True) or ""
            lines = text.splitlines()

            for raw in lines:
                line = clean_spaces(raw.replace("\u00a0", " "))
                if not line:
                    continue
                if RE_PAGE_MARK.fullmatch(line):
                    continue

                # novo item
                m_item = RE_ITEM.match(line)
                if m_item:
                    capture = False
                    current_item = int(m_item.group(1))
                    current_catmat = None
                    continue

                # CATMAT
                m_cat = RE_CATMAT.search(line)
                if m_cat:
                    current_catmat = m_cat.group(1)

                # liga/desliga tabela
                if is_table_on(line):
                    capture = True
                    continue
                if is_table_off(line):
                    capture = False
                    continue
                if not capture:
                    continue

                s = normalize_text(line)
                if is_header(s):
                    continue

                # linha do registro
                if RE_ROW_START.match(s):
                    fields = parse_row_fields(s)
                    if not fields:
                        continue

                    inciso = fields["Inciso"]
                    fonte = INCISO_TO_FONTE.get(inciso, "")

                    row = {
                        "Item": f"Item {current_item}" if current_item is not None else None,
                        "CATMAT": current_catmat,
                        "Nº": fields["Nº"],
                        "Inciso": inciso,
                        "Fonte": fonte,
                        "Quantidade": fields["Quantidade"],
                        "Preço unitário": fields["Preço unitário"],
                        "Data": fields["Data"],
                        "Compõe": fields["Compõe"],
                    }
                    records.append(row)
                    debug_records.append(row.copy())

    df = pd.DataFrame(records, columns=FINAL_COLUMNS)

    # somente Compõe=Sim
    if "Compõe" in df.columns:
        df = df[df["Compõe"] == "Sim"].copy()

    df.reset_index(drop=True, inplace=True)

    # garante colunas
    for col in FINAL_COLUMNS:
        if col not in df.columns:
            df[col] = None
    df = df[FINAL_COLUMNS]

    return df, debug_records


def process_pdf_bytes(pdf_bytes: bytes) -> pd.DataFrame:
    df, _ = process_pdf_bytes_debug(pdf_bytes)

    # (opcional) gerar resumo aqui se você quiser no parse.py; mas deixo só o DF "Dados"
    return df


def validate_extraction(df: pd.DataFrame) -> dict:
    return {"total_rows": int(len(df)) if df is not None else 0}


def debug_dump(df: pd.DataFrame, debug_records: list[dict], max_rows: int = 200) -> str:
    out = []
    out.append("=" * 120)
    out.append("DEBUG DUMP — REGISTROS EXTRAÍDOS (sem coluna Nome; com Fonte)")
    out.append("=" * 120)

    for i, r in enumerate(debug_records[:max_rows], start=1):
        out.append(
            f"[{i:03d}] {r.get('Item')} | CATMAT {r.get('CATMAT')} | Nº {r.get('Nº')} | "
            f"Inciso {r.get('Inciso')} | Fonte {r.get('Fonte')} | "
            f"Qtd {r.get('Quantidade')} | Preço {r.get('Preço unitário')} | "
            f"Data {r.get('Data')} | Compõe {r.get('Compõe')}"
        )

    out.append("")
    out.append(f"Total registros parseados (antes do filtro Compõe=Sim): {len(debug_records)}")
    out.append(f"Total linhas no DF final (Compõe=Sim): {len(df) if df is not None else 0}")
    out.append("=" * 120)
    return "\n".join(out)




# ===============================
# Memoria de Calculo (PDF)
# ===============================

def _preco_txt_to_float_for_memoria(preco_txt: str):
    if preco_txt is None:
        return None
    s = str(preco_txt).strip().replace("R$", "").strip()
    if not s:
        return None
    # PT-BR: 9.309,0000 -> 9309.0000
    s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except Exception:
        return None


def _coef_var(vals):
    if not vals:
        return None
    mean = sum(vals) / len(vals)
    if mean == 0:
        return None
    var = sum((v - mean) ** 2 for v in vals) / len(vals)
    std = var ** 0.5
    return std / mean


def _audit_item(vals, upper=1.25, lower=0.75):
    """Replica o padrao do /api/debug para um unico item."""
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
        "cv_final": _coef_var(final) if final else None,
    }


def build_memoria_calculo_txt(df: pd.DataFrame, payload: dict | None = None) -> str:
    """Gera um relatorio TXT (monoespacado) com o passo a passo dos calculos para TODOS os itens.

    Observacao: o texto inclui marcadores simples para estilos no PDF:
      - <<TITLE>>...<<ENDTITLE>> : titulo (fonte maior, negrito)
      - <<B>>...<<ENDB>>         : negrito
      - <<LINK|URL>>...<<ENDLINK>> : hyperlink
    """
    if df is None or getattr(df, "empty", True):
        return "DF vazio. Nenhuma linha encontrada.\n"


    required = {"Item", "Preço unitário"}
    missing = [c for c in required if c not in df.columns]
    if missing:
        return f"Colunas esperadas ausentes: {missing}. Colunas encontradas: {list(df.columns)}\n"

    def _split_lines(s: str) -> list[str]:
        # "||" significa quebra de linha (conforme solicitado)
        parts = [p.strip() for p in (s or "").split("||")]
        return [p for p in parts if p != ""]

    # helper para CV como percentual PT-BR (duas casas)
    def _cv_pct_txt(cv: float | None) -> str:
        if cv is None:
            return ""
        pct = cv * 100.0
        # duas casas e virgula
        s = f"{pct:.2f}".replace(".", ",")
        return f"{s}%"

    # formatação dinâmica: >= 1 -> 2 casas; < 1 -> 4 casas
    def _num_dyn(v: float | None) -> str:
        if v is None:
            return ""
        dec = 2 if abs(v) >= 1 else 4
        return f"{v:.{dec}f}"

    out: list[str] = []

    payload = payload or {}
    # Mapa com informação do front (último licitado / ajustes manuais)
    relatorio = build_itens_relatorio(df, payload=payload) if df is not None else []
    rel_map = {str(r.get("item")): r for r in relatorio}

    def _append_last_and_final(item_key: str):
        r = rel_map.get(item_key)
        if not r:
            return
        # Observação: o relatório final não deve exibir o "último licitado".
        # Mantemos apenas a indicação do modo e do valor final adotados.
        out.append(f"Modo final adotado: {r.get('modo_final', '')}")
        out.append(f"Valor final adotado: {float_to_preco_txt(_safe_float(r.get('valor_final')), decimals=2)}")
        out.append("")

    def _append_manual_section(item_key: str):
        r = rel_map.get(item_key)
        if not r:
            return
        if r.get("modo_final") != "Manual":
            return
        manual = r.get("manual") or {}
        # Bloco manual
        out.append("<<B>>ANÁLISE MANUAL<<ENDB>>")
        out.append("Valores brutos (numéricos) disponíveis:")
        vals = r.get("valores_brutos") or []
        fontes = r.get("fontes_brutos") or []
        for i, v in enumerate(vals):
            fonte = fontes[i] if i < len(fontes) else ""
            out.append(f"[{i+1}] {_num_dyn(v)} | Fonte: {fonte}")
        out.append("")
        inc = manual.get("included_indices") or []
        inc_1 = []
        for x in inc:
            try:
                inc_1.append(int(x) + 1)
            except Exception:
                pass
        out.append(f"Índices incluídos: {inc_1}")
        out.append(f"Quantidade excluída manualmente: {manual.get('excluded_count', '')}")
        out.append(f"Método escolhido: {manual.get('method', '')}")

        mean = _safe_float(manual.get("mean"))
        median = _safe_float(manual.get("median"))
        cvv = _safe_float(manual.get("cv"))
        out.append(
            f"Média (inclusão manual): {_num_dyn(mean)}" if mean is not None else "Média (inclusão manual):"
        )
        out.append(
            f"Mediana (inclusão manual): {_num_dyn(median)}" if median is not None else "Mediana (inclusão manual):"
        )
        out.append(f"Coeficiente de Variação (inclusão manual): {_cv_pct_txt(cvv)}")
        out.append(
            f"Valor Final (inclusão manual): {float_to_preco_txt(_safe_float(manual.get('valor_final')), decimals=2)}"
        )

        just_txt = (manual.get("justificativa_texto") or "").strip()
        if just_txt:
            out.append(f"Justificativa de análise manual: {just_txt}")
        out.append("")

    # Titulo (duas linhas) com fonte maior
    out.append("<<TITLE>>MEMÓRIA DE CÁLCULO - TABELA COMPARATIVA DE VALORES<<ENDTITLE>>")
    out.append("<<TITLE>>UPDE - HUSM - UFSM<<ENDTITLE>>")
    out.append("")

    # Metodologia com hyperlink
    out.append(
        "<<LINK|https://www.stj.jus.br/publicacaoinstitucional/index.php/MOP/issue/view/2096/showToc>>"
        "Metodologias de exclusão adotadas conforme Manual de Orientação: Pesquisa de Preços - 4ª edição, do Superior Tribunal de Justiça"
        "<<ENDLINK>>"
    )
    out.append("")

    regras = (
        "Se o número de cotações consideradas na pesquisa de pesquisa de preços realizada no ComprasGOV for: ||"
        "\t1. Único, considera-se como cotação única. ||"
        "\t2. Maior que 1 e menor do que 5, é calculado o coeficiente de variação. Caso este seja menor que 0,25, utiliza-se a média; caso maior, utiliza-se a mediana. ||"
        "\t3. Maior ou igual a 5, utiliza-se a exclusão dos preços que se destoam dos demais, para, posteriormente, realizar a média entre os restantes, da seguinte forma: ||"
        "\t\ta) Excluem-se os preços distoantes superiores, realizando o cálculo da média em relação aos demais (valor/média dos demais). Caso esse valor seja superior à 1,25 (25%), considera-se como excessivamente elevado. ||"
        "\t\tb) Excluem-se, dos preços restantes, os distoantes inferiores,  realizando o cálculo da média em relação aos demais (valor/média dos demais). Caso o valor seja inferior à 0,75 (75%), considera-se como inexequível. ||"
        "\t\tc) Realiza-se a média entre os valores restantes."
    )
    out.extend(_split_lines(regras))
    out.append("")

    for item, g_raw in df.groupby("Item", sort=False):
        out.append(f"<<B>>{'_' * 50}<<ENDB>>")
        out.append(f"<<B>>{str(item)}<<ENDB>>")

        g = g_raw.copy()
        g["preco_num"] = g["Preço unitário"].apply(_preco_txt_to_float_for_memoria)
        vals = g["preco_num"].dropna().astype(float).tolist()

        n_bruto = len(g_raw)
        n_parse = len(vals)
        out.append(f"Amostras Iniciais: {n_bruto}")

        if n_parse == 0:
            out.append("Nenhum valor conseguiu ser convertido para número.")
            out.append('Valores originais da coluna "Preço Unitário" (primeiros 50):')
            out.append(", ".join([str(x) for x in g_raw["Preço unitário"].tolist()[:50]]))
            out.append("")
            _append_last_and_final(str(item))
            _append_manual_section(str(item))
            continue

        # Caso com poucos valores
        if n_parse == 1:
            out.append(f"Valor único: {_num_dyn(vals[0])}")
            out.append("Preço Final Escolhido: Valor único.")
            out.append(f"Valor escolhido: {float_to_preco_txt(vals[0], decimals=2)}")
            out.append("")
            _append_last_and_final(str(item))
            _append_manual_section(str(item))
            continue

        # N < 5 -> CV decide
        if n_parse < 5:
            cv = _coef_var(vals)
            mean = sum(vals) / len(vals)
            med = float(pd.Series(vals).median())
            out.append("Valores Iniciais considerados no cálculo:")
            out.append(", ".join([_num_dyn(v) for v in vals]))
            out.append("")
            out.append(f"Média: {_num_dyn(mean)}")
            out.append(f"Mediana: {_num_dyn(med)}")
            out.append(f"CV: {_cv_pct_txt(cv)}")

            if cv is None:
                escolhido = "Mediana"
                valor = med
                motivo = "CV indefinido (média=0)"
            elif cv < 0.25:
                escolhido = "Média"
                valor = mean
                motivo = "CV < 0,25"
            else:
                escolhido = "Mediana"
                valor = med
                motivo = "CV >= 0,25"

            out.append(f"Decisão: {escolhido} ({motivo})")
            out.append(f"Valor Final: {float_to_preco_txt(valor, decimals=2)}")
            out.append("")
            _append_last_and_final(str(item))
            _append_manual_section(str(item))
            continue

        # N >= 5 -> filtro e media
        rep = _audit_item(vals, upper=1.25, lower=0.75)

        out.append("Valores Iniciais considerados no cálculo:")
        out.append(", ".join([_num_dyn(v) for v in rep["iniciais"]]))
        out.append("")

        out.append("--- Preços exclúidos por serem Excessivamente Elevados ---")
        out.append(f"Quantidade: {len(rep['excluidos_altos'])}")
        for r in rep["excluidos_altos"]:
            out.append(
                f"Valor={_num_dyn(r['v'])} | Média dos demais={_num_dyn(r['m_outros'])} | Proporção={r['ratio']:.4f}"
            )
        out.append("")

        out.append("Mantidos após exclusão dos Excessivamente Elevados:")
        out.append(", ".join([_num_dyn(v) for v in rep["apos_alto"]]))
        out.append("")

        out.append("--- Preços exclúidos por serem Inexequíveis ---")
        out.append(f"Quantidade: {len(rep['excluidos_baixos'])}")
        for r in rep["excluidos_baixos"]:
            out.append(
                f"Valor={_num_dyn(r['v'])} | Média dos demais={_num_dyn(r['m_outros'])} | Proporção={r['ratio']:.4f}"
            )
        out.append("")

        out.append("Valores considerados no cálculo final:")
        out.append(", ".join([_num_dyn(v) for v in rep["finais"]]))
        out.append(f"Número de valores considerados no cálculo final: {len(rep['finais'])}")
        media_txt = "" if rep["media_final"] is None else _num_dyn(rep["media_final"])
        out.append(f"Média final: {media_txt}")
        out.append(f"Coeficiente de Variação final: {_cv_pct_txt(rep['cv_final'])}")

        valor2 = rep["media_final"]
        out.append("Decisão Final: Média")
        val_txt = float_to_preco_txt(valor2, decimals=2) if valor2 is not None else ""
        out.append(f"Valor Final: {val_txt}")
        out.append("")

        _append_last_and_final(str(item))
        _append_manual_section(str(item))

    return "\n".join(out) + "\n"



def _text_to_pdf_bytes(text: str) -> bytes:
    """Renderiza o TXT (com marcadores simples) em PDF com quebra de pagina.

    Marcadores aceitos:
      - <<TITLE>>...<<ENDTITLE>>      : titulo (fonte maior, negrito)
      - <<B>>...<<ENDB>>              : negrito
      - <<LINK|URL>>...<<ENDLINK>>    : hyperlink
    """
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)

    width, height = A4
    left = 36
    right = 36
    top = 36
    bottom = 36
    usable_width = width - left - right

    font_name = "Courier"
    bold_font_name = "Courier-Bold"
    font_size = 9
    line_height = 11

    title_font_size = 12
    title_line_height = 16

    c.setFont(font_name, font_size)

    y = height - top

    # Heuristica de quebra de linha por largura
    # Courier ~ monoespacado: estimativa de caracteres por linha
    avg_char_w = c.stringWidth("M", font_name, font_size)
    max_chars = max(20, int(usable_width // avg_char_w))

    def _page_break_if_needed(curr_font_name: str, curr_font_size: int):
        nonlocal y
        if y <= bottom:
            c.showPage()
            c.setFont(curr_font_name, curr_font_size)
            y = height - top

    def _draw_chunk(s: str, curr_font_name: str, curr_font_size: int, curr_line_height: int, link_url: str | None = None):
        nonlocal y
        _page_break_if_needed(curr_font_name, curr_font_size)
        c.setFont(curr_font_name, curr_font_size)
        c.drawString(left, y, s)

        if link_url:
            w = c.stringWidth(s, curr_font_name, curr_font_size)
            # retangulo de clique (baseline -> caixa aproximada)
            y0 = y - 2
            y1 = y + curr_font_size + 2
            c.linkURL(link_url, (left, y0, left + w, y1), relative=0)

        y -= curr_line_height

    def _strip_marker(line: str):
        # retorna (tipo, payload, url)
        if line.startswith("<<TITLE>>") and line.endswith("<<ENDTITLE>>"):
            return ("TITLE", line[len("<<TITLE>>") : -len("<<ENDTITLE>>")], None)
        if line.startswith("<<B>>") and line.endswith("<<ENDB>>"):
            return ("B", line[len("<<B>>") : -len("<<ENDB>>")], None)
        if line.startswith("<<LINK|") and line.endswith("<<ENDLINK>>"):
            # <<LINK|URL>>texto<<ENDLINK>>
            mid = line.find(">>")
            url = line[len("<<LINK|") : mid]
            payload = line[mid + 2 : -len("<<ENDLINK>>")]
            return ("LINK", payload, url)
        return ("N", line, None)

    for raw_line in (text or "").splitlines():
        raw = raw_line.rstrip("\n")
        kind, payload, url = _strip_marker(raw)

        if kind == "TITLE":
            curr_font = bold_font_name
            curr_size = title_font_size
            curr_lh = title_line_height
            link = None
        elif kind == "B":
            curr_font = bold_font_name
            curr_size = font_size
            curr_lh = line_height
            link = None
        elif kind == "LINK":
            curr_font = font_name
            curr_size = font_size
            curr_lh = line_height
            link = url
        else:
            curr_font = font_name
            curr_size = font_size
            curr_lh = line_height
            link = None

        line = payload
        if len(line) <= max_chars:
            _draw_chunk(line, curr_font, curr_size, curr_lh, link_url=link)
        else:
            start = 0
            while start < len(line):
                chunk = line[start : start + max_chars]
                _draw_chunk(chunk, curr_font, curr_size, curr_lh, link_url=link)
                start += max_chars

    c.save()
    buffer.seek(0)
    return buffer.read()


def build_memoria_calculo_pdf_bytes(df: pd.DataFrame, payload: dict | None = None) -> bytes:
    """Gera o PDF institucional do "Relatório Comparativo de Valores" (Memória de Cálculo).

    Observações:
      - Cabeçalho e rodapé seguem o padrão do relatório "Tabela Final de Preços".
      - Não exibe o "último licitado" (usado apenas como balizador interno).
      - Inclui: metadados da lista, referência metodológica, sumário inicial e detalhamento por item.
    """

    payload = payload or {}
    lista_meta = payload.get("lista_meta") or payload.get("lista") or {}
    if not isinstance(lista_meta, dict):
        lista_meta = {}

    numero_lista = str(lista_meta.get("numero_lista") or "").strip()
    nome_lista = str(lista_meta.get("nome_lista") or "").strip()
    processo_sei = str(lista_meta.get("processo_sei") or "").strip()
    responsavel = str(lista_meta.get("responsavel") or "").strip()

    # ---- helpers de data/hora (PT-BR)
    months = [
        "janeiro",
        "fevereiro",
        "março",
        "abril",
        "maio",
        "junho",
        "julho",
        "agosto",
        "setembro",
        "outubro",
        "novembro",
        "dezembro",
    ]
    now = None
    if ZoneInfo is not None:
        try:
            now = datetime.now(ZoneInfo("America/Sao_Paulo"))
        except Exception:
            now = None
    if now is None:
        now = datetime.now()

    dt_line = f"Relatório gerado em {now.day:02d} de {months[now.month - 1]} de {now.year}, às {now:%H:%M}."

    def _only_item_number(s: str) -> str:
        if s is None:
            return ""
        m = re.search(r"(\d+)", str(s))
        return m.group(1) if m else str(s)

    def _fmt_dyn_num(x: float | None) -> str:
        if x is None:
            return ""
        x = float(x)
        dec = 2 if abs(x) >= 1 else 4
        return f"{x:.{dec}f}".replace(".", ",")

    def _fmt_dyn_brl(x: float | None) -> str:
        if x is None:
            return ""
        x = float(x)
        dec = 2 if abs(x) >= 1 else 4
        return float_to_preco_txt(x, decimals=dec)

    def _cv_pct_txt(cv: float | None) -> str:
        if cv is None:
            return ""
        s = f"{(cv * 100.0):.2f}".replace(".", ",")
        return f"{s}%"

    # ---- carregar logo combinado (base64 -> filesystem)
    def _load_logo_reader() -> ImageReader | None:
        try:
            from parser.logo_b64 import HEADER_LOGO_JPEG_B64

            b64_str = HEADER_LOGO_JPEG_B64 or ""
            if b64_str:
                compact = re.sub(r"\s+", "", b64_str)
                raw = base64.b64decode(compact)
                return ImageReader(io.BytesIO(raw))
        except Exception:
            pass

        try:
            assets_dir = os.path.join(os.path.dirname(__file__), "assets")
            for ext in (".jpg", ".jpeg", ".png"):
                path = os.path.join(assets_dir, f"header{ext}")
                if os.path.exists(path):
                    return ImageReader(path)
        except Exception:
            pass
        return None

    def _fit_size(ir: ImageReader, max_w: float, max_h: float) -> tuple[float, float]:
        w, h = ir.getSize()
        if not w or not h:
            return (0.0, 0.0)
        scale = min(max_w / float(w), max_h / float(h))
        return (float(w) * scale, float(h) * scale)

    header_ir = _load_logo_reader()

    # ---- construir conteúdo
    buf = io.BytesIO()
    page_w, page_h = A4

    # Reservar espaço de cabeçalho/rodapé
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=36,
        rightMargin=36,
        topMargin=92,
        bottomMargin=42,
        title="Relatório Comparativo de Valores",
    )

    styles = getSampleStyleSheet()

    style_title = ParagraphStyle(
        "rcv_title",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=14,
        leading=16,
        alignment=TA_CENTER,
        spaceAfter=4,
    )
    style_subtitle = ParagraphStyle(
        "rcv_subtitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=14,
        alignment=TA_CENTER,
        spaceAfter=8,
    )
    style_meta = ParagraphStyle(
        "rcv_meta",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=10,
        leading=12,
        alignment=TA_CENTER,
        spaceAfter=3,
    )
    style_body = ParagraphStyle(
        "rcv_body",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        alignment=TA_LEFT,
    )
    style_body_bold = ParagraphStyle(
        "rcv_body_bold",
        parent=style_body,
        fontName="Helvetica-Bold",
    )
    style_small_grey = ParagraphStyle(
        "rcv_small_grey",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8,
        leading=10,
        alignment=TA_RIGHT,
        textColor=colors.grey,
    )

    # Faixa do item (cinza claro + borda)
    style_item_band = ParagraphStyle(
        "rcv_item_band",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=10,
        leading=12,
        alignment=TA_LEFT,
    )

    story: list = []

    # ---- cabeçalho/rodapé (canvas)
    def _on_page(canv: canvas.Canvas, _doc):
        canv.saveState()
        try:
            # Cabeçalho
            y_top = page_h - 26
            y_line = page_h - 78
            if header_ir is not None:
                w, h = _fit_size(header_ir, max_w=(page_w - _doc.leftMargin - _doc.rightMargin), max_h=40)
                x = (page_w - w) / 2.0
                canv.drawImage(header_ir, x, y_top - h, width=w, height=h, mask="auto")
            else:
                canv.setFont("Helvetica", 10)
                canv.drawCentredString(page_w / 2.0, y_top - 18, "HUSM | UFSM | EBSERH")

            canv.setStrokeColor(colors.lightgrey)
            canv.setLineWidth(1)
            canv.line(_doc.leftMargin, y_line, page_w - _doc.rightMargin, y_line)

            # Rodapé
            footer_y = 22
            canv.setStrokeColor(colors.lightgrey)
            canv.setLineWidth(0.8)
            canv.line(_doc.leftMargin, footer_y + 12, page_w - _doc.rightMargin, footer_y + 12)

            canv.setFont("Helvetica", 8)
            canv.setFillColor(colors.grey)
            canv.drawCentredString(page_w / 2.0, footer_y, f"Página {canv.getPageNumber()}")
            canv.drawRightString(page_w - _doc.rightMargin, footer_y, dt_line)
        except Exception:
            pass
        canv.restoreState()

    # ---- título e metadados
    story.append(Paragraph("RELATÓRIO COMPARATIVO DE VALORES", style_title))
    story.append(Paragraph("MEMÓRIA DE CÁLCULO", style_subtitle))
    if numero_lista or nome_lista:
        story.append(Paragraph(f"LISTA {numero_lista} | {nome_lista}".strip(), style_meta))
    if processo_sei or responsavel:
        if processo_sei and responsavel:
            story.append(Paragraph(f"PROCESSO SEI {processo_sei} | RESPONSÁVEL: {responsavel}".strip(), style_meta))
        elif processo_sei:
            story.append(Paragraph(f"PROCESSO SEI {processo_sei}".strip(), style_meta))
        else:
            story.append(Paragraph(f"RESPONSÁVEL: {responsavel}".strip(), style_meta))
    story.append(Spacer(1, 10))

    # ---- referência metodológica
    story.append(Paragraph("REFERÊNCIA METODOLÓGICA", style_body_bold))
    story.append(
        Paragraph(
            "A estimativa de preços foi calculada conforme as metodologias de exclusão e cálculo descritas no "
            "<link href='https://www.stj.jus.br/publicacaoinstitucional/index.php/MOP/issue/view/2096/showToc' color='blue'>"
            "Manual de Orientação: Pesquisa de Preços (4ª edição) do Superior Tribunal de Justiça (STJ)"
            "</link>. "
            "A aplicação das regras segue o quantitativo de amostras obtidas no ComprasGOV:"
            ,
            style_body,
        )
    )
    story.append(Spacer(1, 6))
    # regras em lista (mais formal)
    regras_tbl = Table(
        [
            [Paragraph("1) Cotação única: considera-se como cotação única.", style_body)],
            [Paragraph("2) Entre 2 e 4 cotações: calcula-se o coeficiente de variação (CV). Se CV &lt; 0,25 utiliza-se a média; caso contrário, utiliza-se a mediana.", style_body)],
            [Paragraph("3) Com 5 ou mais cotações: realiza-se a exclusão de valores destoantes e, em seguida, calcula-se a média dos valores remanescentes.", style_body)],
            [Paragraph("&nbsp;&nbsp;&nbsp;a) Excluem-se valores excessivamente elevados: (valor / média dos demais) &gt; 1,25.", style_body)],
            [Paragraph("&nbsp;&nbsp;&nbsp;b) Excluem-se valores inexequíveis: (valor / média dos demais) &lt; 0,75.", style_body)],
            [Paragraph("&nbsp;&nbsp;&nbsp;c) Calcula-se a média final dos valores restantes.", style_body)],
        ],
        colWidths=[doc.width],
    )
    regras_tbl.setStyle(
        TableStyle(
            [
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    story.append(regras_tbl)
    story.append(Spacer(1, 12))

    # ---- sumário inicial
    itens_relatorio = build_itens_relatorio(df, payload=payload) if df is not None else []
    story.append(Paragraph("SUMÁRIO", style_body_bold))
    story.append(Spacer(1, 6))

    sum_header = [
        "ITEM",
        "CATMAT",
        "AMOSTRAS INICIAIS",
        "AMOSTRAS FINAIS",
        "E.E.",
        "INEXEQ.",
        "MODO",
        "MÉTODO",
        "VALOR (R$)",
    ]
    sum_data = [sum_header]
    for it in itens_relatorio or []:
        sum_data.append(
            [
                _only_item_number(it.get("item", "")),
                str(it.get("catmat", "")),
                str(it.get("n_bruto", "")),
                str(it.get("n_final_final") or it.get("n_final_auto", "")),
                str(it.get("excl_altos", "")),
                str(it.get("excl_baixos", "")),
                str(it.get("modo_final", "")),
                str(it.get("metodo_final", "")),
                _fmt_dyn_brl(_safe_float(it.get("valor_final"))),
            ]
        )

    sum_col_widths = [40, 60, 85, 85, 45, 55, 55, 60, 70]
    sum_tbl = Table(sum_data, repeatRows=1, colWidths=sum_col_widths, hAlign="CENTER")
    sum_tbl.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 8),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("FONTSIZE", (0, 1), (-1, -1), 8),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(sum_tbl)
    story.append(PageBreak())

    # ---- detalhamento por item
    rel_map = {str(r.get("item")): r for r in itens_relatorio}

    for item, g_raw in df.groupby("Item", sort=False):
        item_key = str(item)
        r = rel_map.get(item_key) or {}
        item_num = _only_item_number(item_key)

        # faixa do item (cinza claro) + borda
        band_tbl = Table([[Paragraph(f"ITEM {item_num}", style_item_band)]], colWidths=[doc.width])
        band_tbl.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.whitesmoke),
                    ("BOX", (0, 0), (-1, -1), 0.6, colors.grey),
                    ("LINEBELOW", (0, 0), (-1, -1), 1.0, colors.grey),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )

        blocks: list = [band_tbl, Spacer(1, 10)]

        # preparar valores
        g = g_raw.copy()
        g["preco_num"] = g["Preço unitário"].apply(_preco_txt_to_float_for_memoria)
        vals = g["preco_num"].dropna().astype(float).tolist()
        n_bruto = int(len(g_raw))
        n_parse = int(len(vals))

        modo_final = str(r.get("modo_final") or "")
        metodo_final = str(r.get("metodo_final") or "")
        valor_final = _safe_float(r.get("valor_final"))

        # bloco resumo do item
        resumo_pairs = [
            [Paragraph("Amostras Iniciais", style_body_bold), Paragraph(str(n_bruto), style_body)],
                    ]
        if modo_final:
            resumo_pairs.append([Paragraph("Modo final adotado", style_body_bold), Paragraph(modo_final, style_body)])
        if metodo_final:
            resumo_pairs.append([Paragraph("Método", style_body_bold), Paragraph(metodo_final, style_body)])
        if valor_final is not None:
            resumo_pairs.append([Paragraph("Valor Final", style_body_bold), Paragraph(_fmt_dyn_brl(valor_final), style_body)])

        resumo_tbl = Table(resumo_pairs, colWidths=[160, doc.width - 160])
        resumo_tbl.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ("TOPPADDING", (0, 0), (-1, -1), 2),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ]
            )
        )
        blocks.append(resumo_tbl)
        blocks.append(Spacer(1, 8))

        if n_parse == 0:
            blocks.append(Paragraph("Nenhum valor conseguiu ser convertido para número.", style_body))
            blocks.append(Spacer(1, 10))
            story.append(KeepTogether(blocks))
            continue

        # lista de valores iniciais (dinâmica)
        vals_txt = " | ".join([_fmt_dyn_num(v) for v in vals])
        blocks.append(Paragraph("Valores iniciais considerados no cálculo:", style_body_bold))
        blocks.append(Paragraph(f"<i>{vals_txt}</i>", style_body))
        blocks.append(Spacer(1, 8))

        # casos
        if n_parse == 1:
            blocks.append(Paragraph(f"Valor único: {_fmt_dyn_num(vals[0])}", style_body))
            blocks.append(Spacer(1, 10))
            story.append(KeepTogether(blocks))
            continue

        if n_parse < 5:
            cvv = _coef_var(vals)
            mean_v = sum(vals) / len(vals)
            med_v = float(pd.Series(vals).median())
            blocks.append(Paragraph(f"Média: {_fmt_dyn_num(mean_v)}", style_body))
            blocks.append(Paragraph(f"Mediana: {_fmt_dyn_num(med_v)}", style_body))
            blocks.append(Paragraph(f"Coeficiente de Variação (CV): {_cv_pct_txt(cvv)}", style_body))
            blocks.append(Spacer(1, 10))
            story.append(KeepTogether(blocks))
            continue

        rep = _audit_item(vals, upper=1.25, lower=0.75)

        # excessivamente elevados
        blocks.append(Paragraph("Preços excluídos por serem Excessivamente Elevados:", style_body_bold))
        blocks.append(Paragraph(f"Quantidade: {len(rep.get('excluidos_altos') or [])}", style_body))
        for rr in rep.get("excluidos_altos") or []:
            blocks.append(
                Paragraph(
                    f"Valor={_fmt_dyn_num(rr.get('v'))} | Média dos demais={_fmt_dyn_num(rr.get('m_outros'))} | Proporção={rr.get('ratio', 0):.4f}",
                    style_body,
                )
            )
        blocks.append(Spacer(1, 6))

        blocks.append(Paragraph("Mantidos após exclusão dos Excessivamente Elevados:", style_body_bold))
        _apos_alto_txt = " | ".join([_fmt_dyn_num(v) for v in rep.get("apos_alto") or []])
        blocks.append(Paragraph(f"<i>{_apos_alto_txt}</i>", style_body))
        blocks.append(Spacer(1, 8))

        # inexequíveis
        blocks.append(Paragraph("Preços excluídos por serem Inexequíveis:", style_body_bold))
        blocks.append(Paragraph(f"Quantidade: {len(rep.get('excluidos_baixos') or [])}", style_body))
        for rr in rep.get("excluidos_baixos") or []:
            blocks.append(
                Paragraph(
                    f"Valor={_fmt_dyn_num(rr.get('v'))} | Média dos demais={_fmt_dyn_num(rr.get('m_outros'))} | Proporção={rr.get('ratio', 0):.4f}",
                    style_body,
                )
            )
        blocks.append(Spacer(1, 6))

        finais = rep.get("finais") or []
        blocks.append(Paragraph("Valores considerados no cálculo final:", style_body_bold))
        _finais_txt = " | ".join([_fmt_dyn_num(v) for v in finais])
        blocks.append(Paragraph(f"<i>{_finais_txt}</i>", style_body))
        blocks.append(Spacer(1, 4))
        blocks.append(Paragraph(f"Número de valores considerados no cálculo final: {len(finais)}", style_body))
        blocks.append(Paragraph(f"Média final: {_fmt_dyn_num(rep.get('media_final'))}", style_body))
        blocks.append(Paragraph(f"Coeficiente de Variação final: {_cv_pct_txt(rep.get('cv_final'))}", style_body))
        blocks.append(Spacer(1, 8))

        # bloco manual, se existir
        if str(r.get("modo_final") or "") == "Manual":
            blocks.append(Paragraph("ANÁLISE MANUAL", style_body_bold))
            blocks.append(Spacer(1, 4))
            vals_brutos = r.get("valores_brutos") or []
            fontes_brutos = r.get("fontes_brutos") or []
            manual = r.get("manual") or {}
            included = set()
            for idx in manual.get("included_indices") or []:
                try:
                    included.add(int(idx))
                except Exception:
                    pass

            # construir tabela de valores brutos com fonte (ordenados por valor)
            rows = []
            for i, v in enumerate(vals_brutos):
                fonte = fontes_brutos[i] if i < len(fontes_brutos) else ""
                rows.append({"idx": i, "idx1": i + 1, "valor": float(v), "fonte": str(fonte)})
            rows.sort(key=lambda x: x["valor"])

            man_table = [["ÍNDICE", "VALOR", "FONTE", "INCLUÍDO"]]
            for rr in rows:
                man_table.append(
                    [
                        str(rr["idx1"]),
                        _fmt_dyn_num(rr["valor"]),
                        rr["fonte"],
                        "Sim" if rr["idx"] in included else "Não",
                    ]
                )
            mt = Table(man_table, repeatRows=1, colWidths=[50, 70, doc.width - 50 - 70 - 60, 60])
            mt.setStyle(
                TableStyle(
                    [
                        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                        ("FONTSIZE", (0, 0), (-1, 0), 8),
                        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                        ("FONTSIZE", (0, 1), (-1, -1), 8),
                        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
                        ("LEFTPADDING", (0, 0), (-1, -1), 4),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                        ("TOPPADDING", (0, 0), (-1, -1), 3),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                    ]
                )
            )
            blocks.append(mt)
            blocks.append(Spacer(1, 6))

            mean_m = _safe_float(manual.get("mean"))
            median_m = _safe_float(manual.get("median"))
            cv_m = _safe_float(manual.get("cv"))
            valor_m = _safe_float(manual.get("valor_final"))
            blocks.append(Paragraph(f"Média (inclusão manual): {_fmt_dyn_num(mean_m)}", style_body))
            blocks.append(Paragraph(f"Mediana (inclusão manual): {_fmt_dyn_num(median_m)}", style_body))
            blocks.append(
                Paragraph(f"Coeficiente de Variação (inclusão manual): {_cv_pct_txt(cv_m)}", style_body)
            )
            blocks.append(Paragraph(f"Valor Final (inclusão manual): {_fmt_dyn_brl(valor_m)}", style_body))

            just_txt = str(manual.get("justificativa_texto") or "").strip()
            if just_txt:
                blocks.append(Paragraph(f"Justificativa de análise manual: {just_txt}", style_body))

        blocks.append(Spacer(1, 12))
        story.append(KeepTogether(blocks))

    doc.build(story, onFirstPage=_on_page, onLaterPages=_on_page)
    buf.seek(0)
    return buf.read()


def _fmt_brl(x: float | None) -> str:
    if x is None:
        return ""
    x = float(x)
    decimals = 2 if abs(x) >= 1 else 4
    return float_to_preco_txt(x, decimals=decimals)


def build_pdf_tabela_comparativa_bytes(itens_relatorio: list[dict], meta: dict | None = None) -> bytes:
    """Gera o PDF "Tabela Final de Preços" (bytes) com identidade visual institucional."""
    meta = meta or {}
    numero_lista = str(meta.get("numero_lista") or "").strip()
    nome_lista = str(meta.get("nome_lista") or "").strip()
    processo_sei = str(meta.get("processo_sei") or "").strip()
    responsavel = str(meta.get("responsavel") or "").strip()

    # ---- helpers
    def _only_item_number(s: str) -> str:
        if s is None:
            return ""
        m = re.search(r"(\d+)", str(s))
        return m.group(1) if m else str(s)

    def _load_logo_reader(kind: str) -> ImageReader | None:
        """Carrega logo como ImageReader.

        Prioridade:
          1) base64 (arquivo parser/logo_b64.py)
          2) arquivo em parser/assets/<kind>.jpg/.jpeg/.png

        Observação: desenhamos as imagens diretamente no canvas (onFirstPage/onLaterPages),
        pois isso é mais robusto no deploy (Vercel) do que usar imagens como Flowables.
        """
        # 1) base64
        try:
            from parser.logo_b64 import HEADER_LOGO_JPEG_B64

            b64_map = {
                "header": HEADER_LOGO_JPEG_B64,
            }
            b64_str = b64_map.get(kind, "")
            if b64_str:
                # remove quebras de linha/espacos para garantir decode correto
                compact = re.sub(r"\s+", "", b64_str)
                raw = base64.b64decode(compact)
                return ImageReader(io.BytesIO(raw))
        except Exception:
            pass

        # 2) filesystem
        try:
            assets_dir = os.path.join(os.path.dirname(__file__), "assets")
            # Preferir JPEG (não depende do Pillow). PNG pode falhar sem Pillow.
            for ext in (".jpg", ".jpeg", ".png"):
                path = os.path.join(assets_dir, f"{kind}{ext}")
                if os.path.exists(path):
                    return ImageReader(path)
        except Exception:
            pass
        return None

    def _fit_size(ir: ImageReader, max_w: float, max_h: float) -> tuple[float, float]:
        w, h = ir.getSize()
        if not w or not h:
            return (0.0, 0.0)
        scale = min(max_w / float(w), max_h / float(h))
        return (float(w) * scale, float(h) * scale)

    # ---- title/subtitle (CAIXA ALTA + NEGRITO)
    title_main = "TABELA FINAL DE PREÇOS"
    if numero_lista or nome_lista:
        title_main = f"TABELA FINAL DE PREÇOS - LISTA {numero_lista} | {nome_lista}".strip()

    subtitle = ""
    if processo_sei and responsavel:
        subtitle = f"PROCESSO SEI {processo_sei} | RESPONSÁVEL: {responsavel}".strip()
    elif processo_sei:
        subtitle = f"PROCESSO SEI {processo_sei}".strip()
    elif responsavel:
        subtitle = f"RESPONSÁVEL: {responsavel}".strip()


    # ---- datetime (PT-BR, minúsculas)
    months = [
        "janeiro",
        "fevereiro",
        "março",
        "abril",
        "maio",
        "junho",
        "julho",
        "agosto",
        "setembro",
        "outubro",
        "novembro",
        "dezembro",
    ]
    now = None
    if ZoneInfo is not None:
        try:
            now = datetime.now(ZoneInfo("America/Sao_Paulo"))
        except Exception:
            now = None
    if now is None:
        now = datetime.now()

    dt_line = f"Relatório gerado em {now.day:02d} de {months[now.month - 1]} de {now.year}, às {now:%H:%M}."

    buf = io.BytesIO()

    # Reservar espaço para cabeçalho com logos (desenhado no canvas)
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),
        leftMargin=24,
        rightMargin=24,
        topMargin=78,
        bottomMargin=42,
        title="Tabela Final de Preços",
    )

    styles = getSampleStyleSheet()

    style_title = ParagraphStyle(
        "title",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=14,
        leading=16,
        alignment=TA_CENTER,
        spaceAfter=4,
    )
    style_sub = ParagraphStyle(
        "sub",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=13,
        alignment=TA_CENTER,
        spaceAfter=8,
    )
    style_normal = ParagraphStyle(
        "normal",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=11,
        alignment=TA_LEFT,
    )
    style_small_center = ParagraphStyle(
        "small_center",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8,
        leading=10,
        alignment=TA_CENTER,
        textColor=colors.grey,
    )
    style_date = ParagraphStyle(
        "date",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8,
        leading=10,
        alignment=TA_RIGHT,
        textColor=colors.grey,
    )
    style_head_cell = ParagraphStyle(
        "head_cell",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=9,
        alignment=TA_CENTER,
    )

    story: list = []

    # ---- Cabeçalho com logos (canvas)
    # Desenhamos direto no canvas para ser mais confiável no deploy (Vercel) e em diferentes viewers.
    page_w, page_h = landscape(A4)
    header_ir = _load_logo_reader("header")

    def _draw_header(canv: canvas.Canvas, _doc):
        canv.saveState()
        try:
            # área do cabeçalho
            y_top = page_h - 26
            y_line = page_h - 78

            # Logo combinado centralizado
            if header_ir is not None:
                w, h = _fit_size(header_ir, max_w=(page_w - _doc.leftMargin - _doc.rightMargin), max_h=40)
                x = (page_w - w) / 2.0
                canv.drawImage(header_ir, x, y_top - h, width=w, height=h, mask='auto')
            else:
                # fallback simples (não esperado): texto centralizado
                canv.setFont("Helvetica", 10)
                canv.drawCentredString(page_w / 2.0, y_top - 18, "HUSM | UFSM | EBSERH")

            # linha separadora
            canv.setStrokeColor(colors.lightgrey)
            canv.setLineWidth(1)
            canv.line(_doc.leftMargin, y_line, page_w - _doc.rightMargin, y_line)
        except Exception:
            pass
        canv.restoreState()

    # ---- Título e Processo SEI (centralizados, CAIXA ALTA + NEGRITO)
    story.append(Paragraph(title_main, style_title))
    if subtitle:
        story.append(Paragraph(subtitle, style_sub))
    else:
        story.append(Spacer(1, 6))

    # ---- Termo de referência metodológica (curto e formal)
    story.append(
        Paragraph(
            "<b>Referência Metodológica</b>: a estimativa de preços foi calculada conforme as metodologias de exclusão e cálculo descritas no Manual de Orientação: Pesquisa de Preços (4ª edição) do Superior Tribunal de Justiça (STJ), com detalhamento no Relatório Comparativo de Valores anexo.",
            style_normal,
        )
    )
    story.append(Spacer(1, 10))

    # ---- Tabela
    header = [
        "ITEM",
        "CATMAT",
        "AMOSTRAS INICIAIS",
        "AMOSTRAS FINAIS",
        "EXCESSIVAMENTE ELEVADOS",
        "INEXEQUÍVEIS",
        "MODO ESTIMATIVA",
        "MÉTODO",
        "VALOR ESTIMADO (R$)",
    ]

    data = [[Paragraph(h, style_head_cell) for h in header]]

    for it in itens_relatorio or []:
        item_num = _only_item_number(it.get("item", ""))
        catmat = str(it.get("catmat", ""))
        ai = str(it.get("n_bruto", ""))
        af = str(it.get("n_final_final") or it.get("n_final_auto", ""))
        ee = str(it.get("excl_altos", ""))
        inex = str(it.get("excl_baixos", ""))
        modo = str(it.get("modo_final", ""))
        metodo = str(it.get("metodo_final", ""))
        valor_final = _safe_float(it.get("valor_final"))

        data.append(
            [
                item_num,
                catmat,
                ai,
                af,
                ee,
                inex,
                modo,
                metodo,
                _fmt_brl(valor_final),
            ]
        )

    # larguras equilibradas p/ caber em paisagem
    col_widths = [45, 75, 95, 95, 135, 95, 110, 85, 95]

    t = Table(data, repeatRows=1, colWidths=col_widths, hAlign="CENTER")
    t.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 8),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("FONTSIZE", (0, 1), (-1, -1), 8),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )

    story.append(t)
    story.append(Spacer(1, 12))

    # ---- Cabeçalho + rodapé (canvas)
    def _on_page(canv: canvas.Canvas, _doc):
        _draw_header(canv, _doc)
        try:
            footer_y = 20
            canv.saveState()
            canv.setStrokeColor(colors.lightgrey)
            canv.setLineWidth(0.8)
            canv.line(_doc.leftMargin, footer_y + 10, page_w - _doc.rightMargin, footer_y + 10)
            canv.setFont("Helvetica", 8)
            canv.setFillColor(colors.grey)
            canv.drawCentredString(page_w / 2.0, footer_y, f"Página {canv.getPageNumber()}")
            canv.drawRightString(page_w - _doc.rightMargin, footer_y, dt_line)
            canv.restoreState()
        except Exception:
            try:
                canv.restoreState()
            except Exception:
                pass

    doc.build(story, onFirstPage=_on_page, onLaterPages=_on_page)

    buf.seek(0)
    return buf.read()
