import re
import io
import json
import pdfplumber
import pandas as pd

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

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
    qty_pat = re.compile(r"^\d{1,3}(?:\.\d{3})*(?:[\.,]\d+)?$")

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

    # Quantidade: procurar número antes do preço
    qtd = None
    for j in range(preco_idx - 1, 1, -1):
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

        # valores brutos (numéricos) na ordem das linhas
        g = g_raw.copy()
        g["preco_num"] = g["Preço unitário"].apply(preco_txt_to_float)
        valores_brutos_all = g["preco_num"].tolist()
        # lista de valores numéricos e também um mapping índice->valor para seleção manual
        valores_brutos = []
        idx_map = []  # índices na lista original para cada valor numérico
        for i, v in enumerate(valores_brutos_all):
            fv = _safe_float(v)
            if fv is None:
                continue
            valores_brutos.append(float(fv))
            idx_map.append(i)

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
            valores_finais_auto = valores_brutos[:]
            n_final = len(valores_finais_auto)
            cv_final = cv
        else:
            vals_filtrados, excl_alto, excl_baixo = filtrar_outliers_por_ratio(valores_brutos, upper=1.25, lower=0.75)
            valores_finais_auto = vals_filtrados[:]
            n_final = len(valores_finais_auto)
            valor_auto = _mean(valores_finais_auto) if n_final > 0 else None
            metodo_auto = "Média"
            cv_final = _cv(valores_finais_auto) if n_final > 0 else None

        # --------- último licitado
        last_quote_val = last_quotes.get(item)
        last_quote = _safe_float(last_quote_val)

        # --------- decisão final (auto vs manual)
        modo = "Auto"
        valor_final = valor_auto
        metodo_final = metodo_auto
        valores_finais = valores_finais_auto[:]
        manual_info = None

        # Só aceita override manual quando último licitado > valor auto (conforme regra do front)
        allow_manual = (last_quote is not None and valor_auto is not None and valor_auto < last_quote)

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
                "valor_auto": valor_auto,
                "metodo_auto": metodo_auto,
                "n_final_auto": int(len(valores_finais_auto)),
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
                "Número de entradas finais": it.get("n_final_auto"),
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

    excel_out = io.BytesIO()
    with pd.ExcelWriter(excel_out, engine="openpyxl") as writer:
        (df or pd.DataFrame()).to_excel(writer, index=False, sheet_name="Dados")
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

    out: list[str] = []

    payload = payload or {}
    # Mapa com informação do front (último licitado / ajustes manuais)
    relatorio = build_itens_relatorio(df, payload=payload) if df is not None else []
    rel_map = {str(r.get("item")): r for r in relatorio}

    def _append_last_and_final(item_key: str):
        r = rel_map.get(item_key)
        if not r:
            return
        lastq = _safe_float(r.get("last_quote"))
        if lastq is None:
            return
        out.append(f"Último licitado (informado): {float_to_preco_txt(lastq, decimals=2)}")
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
        for i, v in enumerate(vals):
            out.append(f"[{i}] {v:.4f}")
        out.append("")
        inc = manual.get("included_indices") or []
        out.append(f"Índices incluídos: {inc}")
        out.append(f"Quantidade excluída manualmente: {manual.get('excluded_count', '')}")
        out.append(f"Método escolhido: {manual.get('method', '')}")

        mean = _safe_float(manual.get("mean"))
        median = _safe_float(manual.get("median"))
        cv = _safe_float(manual.get("cv"))
        out.append(f"Média (dos incluídos): {(mean if mean is not None else 0):.4f}" if mean is not None else "Média (dos incluídos):")
        out.append(f"Mediana (dos incluídos): {(median if median is not None else 0):.4f}" if median is not None else "Mediana (dos incluídos):")
        out.append(f"Coeficiente de Variação (dos incluídos): {_cv_pct_txt(cv)}")
        out.append(f"Valor Final (manual): {float_to_preco_txt(_safe_float(manual.get('valor_final')), decimals=2)}")

        just_code = (manual.get("justificativa_codigo") or "").strip()
        just_txt = (manual.get("justificativa_texto") or "").strip()
        if just_code:
            out.append(f"Justificativa (código): {just_code}")
        if just_txt:
            out.append(f"Justificativa (texto): {just_txt}")
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
        out.append(f"Amostrar Iniciais: {n_bruto}")

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
            out.append(f"Valor único: {vals[0]:.4f}")
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
            out.append(", ".join([f"{v:.4f}" for v in vals]))
            out.append("")
            out.append(f"Média: {mean:.4f}")
            out.append(f"Mediana: {med:.4f}")
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
        out.append(", ".join([f"{v:.4f}" for v in rep["iniciais"]]))
        out.append("")

        out.append("--- Preços exclúidos por serem Excessivamente Elevados ---")
        out.append(f"Quantidade: {len(rep['excluidos_altos'])}")
        for r in rep["excluidos_altos"]:
            out.append(
                f"Valor={r['v']:.4f} | Média dos demais={r['m_outros']:.4f} | Proporção={r['ratio']:.4f}"
            )
        out.append("")

        out.append("Mantidos após exclusão dos Excessivamente Elevados:")
        out.append(", ".join([f"{v:.4f}" for v in rep["apos_alto"]]))
        out.append("")

        out.append("--- Preços exclúidos por serem Inexequíveis ---")
        out.append(f"Quantidade: {len(rep['excluidos_baixos'])}")
        for r in rep["excluidos_baixos"]:
            out.append(
                f"Valor={r['v']:.4f} | Média dos demais={r['m_outros']:.4f} | Proporção={r['ratio']:.4f}"
            )
        out.append("")

        out.append("Valores considerados no cálculo final:")
        out.append(", ".join([f"{v:.4f}" for v in rep["finais"]]))
        out.append(f"Número de valores considerados no cálculo final: {len(rep['finais'])}")
        media_txt = "" if rep["media_final"] is None else f"{rep['media_final']:.4f}"
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
    """Gera o PDF 'Memoria de Calculo' (bytes) a partir do dataframe final (Compõe=Sim).

    payload pode conter último licitado + ajustes manuais para registrar no PDF.
    """
    txt = build_memoria_calculo_txt(df, payload=payload)
    return _text_to_pdf_bytes(txt)


def _fmt_brl(x: float | None) -> str:
    if x is None:
        return ""
    return float_to_preco_txt(float(x), decimals=2)


def build_pdf_tabela_comparativa_bytes(itens_relatorio: list[dict]) -> bytes:
    """Gera o PDF 'Tabela Comparativa de Valores' (bytes).

    A ideia é ser um PDF oficial, com uma tabela clara e repetição de cabeçalho.
    """
    buf = io.BytesIO()

    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),
        leftMargin=24,
        rightMargin=24,
        topMargin=24,
        bottomMargin=24,
        title="Tabela Comparativa de Valores",
    )

    styles = getSampleStyleSheet()
    style_title = styles["Title"]
    style_normal = styles["Normal"]

    story = []
    story.append(Paragraph("Tabela Comparativa de Valores — UPDE / HUSM / UFSM", style_title))
    story.append(Spacer(1, 12))

    header = [
        "Item",
        "Catmat",
        "N iniciais",
        "N finais",
        "Excl. altos",
        "Excl. baixos",
        "Valor calculado",
        "Último licitado",
        "Modo",
        "Método",
        "Valor final",
        "Dif. (R$)",
        "Dif. (%)",
    ]

    data = [header]
    for it in itens_relatorio or []:
        valor_auto = _safe_float(it.get("valor_auto"))
        lastq = _safe_float(it.get("last_quote"))
        valor_final = _safe_float(it.get("valor_final"))

        diff_abs = None
        diff_pct = None
        if valor_final is not None and lastq is not None:
            diff_abs = valor_final - lastq
            if lastq != 0:
                diff_pct = (diff_abs / lastq) * 100.0

        data.append(
            [
                str(it.get("item", "")),
                str(it.get("catmat", "")),
                str(it.get("n_bruto", "")),
                str(it.get("n_final_auto", "")),
                str(it.get("excl_altos", "")),
                str(it.get("excl_baixos", "")),
                _fmt_brl(valor_auto),
                _fmt_brl(lastq),
                str(it.get("modo_final", "")),
                str(it.get("metodo_final", "")),
                _fmt_brl(valor_final),
                _fmt_brl(diff_abs),
                (f"{diff_pct:.2f}%".replace(".", ",") if diff_pct is not None else ""),
            ]
        )

    t = Table(data, repeatRows=1)
    t.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 9),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("FONTSIZE", (0, 1), (-1, -1), 8),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
            ]
        )
    )

    story.append(t)
    story.append(Spacer(1, 12))
    story.append(
        Paragraph(
            "Obs.: Itens em modo 'Pendente' indicam que o último licitado é maior que o valor calculado, mas não houve ajuste manual aplicado.",
            style_normal,
        )
    )

    doc.build(story)
    buf.seek(0)
    return buf.read()
