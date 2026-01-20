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


# =========================================================
# Validação do tipo de relatório (Resumido vs Detalhado)
# =========================================================

class PdfIncompatibilityError(Exception):
    """Erro para indicar PDF incompatível (mensagem amigável para o usuário)."""
    pass


def _detect_report_type_or_raise(pdf_bytes: bytes) -> str:
    """
    Verifica, na primeira página do PDF, se é 'Relatório Resumido' ou 'Relatório Detalhado'.

    Regras:
    - Se tiver 'Relatório Resumido' -> OK
    - Se tiver 'Relatório Detalhado' -> erro: usuário carregou PDF errado
    - Se não tiver nenhum -> erro de incompatibilidade
    """
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            if not pdf.pages:
                raise PdfIncompatibilityError("PDF inválido: não foi possível ler páginas do arquivo.")
            first_text = (pdf.pages[0].extract_text() or "")
    except PdfIncompatibilityError:
        raise
    except Exception:
        raise PdfIncompatibilityError("PDF inválido: não foi possível ler o conteúdo da primeira página.")

    t = first_text.lower()

    has_resumido = ("relatório resumido" in t) or ("relatorio resumido" in t)
    has_detalhado = ("relatório detalhado" in t) or ("relatorio detalhado" in t)

    if has_resumido:
        return "resumido"

    if has_detalhado:
        raise PdfIncompatibilityError(
            "PDF incorreto: você carregou o **Relatório Detalhado**. "
            "Por favor, gere e envie a versão **Relatório Resumido**."
        )

    raise PdfIncompatibilityError(
        "PDF incompatível: não foi possível identificar **Relatório Resumido** nem **Relatório Detalhado** "
        "na primeira página. Verifique se o arquivo é a versão resumida correta do ComprasGOV."
    )


# =========================================================
# Regex / Constantes
# =========================================================

# Aceita "Item: 1" e "Item 1"
RE_ITEM = re.compile(r"^\s*Item\s*:?\s*(\d+)\b", re.IGNORECASE)

# Catmat aparece como "123456 - ..."
RE_CATMAT = re.compile(r"(\d{6})\s*-\s*")

RE_PAGE_MARK = re.compile(r"^\s*\d+\s+de\s+\d+\s*$", re.IGNORECASE)
RE_DATE_TOKEN = re.compile(r"^\d{2}/\d{2}/\d{4}$")

# Linhas de registros normalmente começam com "Nº Inciso" (ex.: "1 I ...")
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


# =========================================================
# Helpers de limpeza / detecção de tabela
# =========================================================

def clean_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def normalize_text(s: str) -> str:
    return clean_spaces(s).replace("\u00a0", " ")


def is_table_on(line: str) -> bool:
    """
    Liga a captura quando:
    - aparece "Período:" (alguns PDFs)
    OU
    - aparece cabeçalho com Nº / Inciso / Quantidade (mais comum)
    """
    l = (line or "").lower()
    if "período" in l or "periodo" in l:
        return True
    return ("nº" in line or "no" in l) and ("inciso" in l) and ("quantidade" in l)


def is_table_off(line: str) -> bool:
    l = (line or "").strip().lower()
    # varia por PDF; mantemos gatilhos conservadores
    return l.startswith("fonte") or l.startswith("nota") or l.startswith("observ")


def is_header(line: str) -> bool:
    l = (line or "").lower()
    return ("preço" in l and "unit" in l and "data" in l) or l.startswith("nº") or l.startswith("no ")


# =========================================================
# Parsing e numéricos
# =========================================================

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
    except Exception:
        return None


def float_to_preco_txt(v, decimals: int = 2) -> str:
    if v is None:
        return ""
    try:
        v = float(v)
    except Exception:
        return ""
    fmt = f"{{:,.{decimals}f}}".format(v)
    fmt = fmt.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {fmt}"


def _safe_float(x):
    try:
        if x is None:
            return None
        return float(x)
    except Exception:
        return None


def _fmt_num_cond(v: float | None) -> str:
    """
    Requisito: se valor >= 1, usar 2 casas; se < 1, usar 4 casas.
    (Para exibição em memória de cálculo / manual.)
    """
    if v is None:
        return ""
    try:
        v = float(v)
    except Exception:
        return ""
    decimals = 2 if abs(v) >= 1 else 4
    return f"{v:.{decimals}f}"


def _fmt_brl_cond(v: float | None) -> str:
    """BRL com 2 casas quando >=1, 4 casas quando <1."""
    if v is None:
        return ""
    try:
        v = float(v)
    except Exception:
        return ""
    decimals = 2 if abs(v) >= 1 else 4
    return float_to_preco_txt(v, decimals=decimals)


def parse_row_fields(line: str) -> dict | None:
    """
    Parser robusto da linha:
    ... Nº Inciso .... Quantidade Preço Data Compõe

    Estratégia:
    - Tokeniza por espaço
    - Procura a DATA de trás pra frente
    - Compõe é o token logo após a data (se existir)
    - Preço é o token anterior à data (ou pode ser "R$" + valor)
    - Quantidade é o token anterior ao preço
    """
    s = normalize_text(line)
    parts = s.split(" ")
    if len(parts) < 6:
        return None

    if not parts[0].isdigit():
        return None

    num = parts[0]
    inciso = parts[1].upper()

    # achar data (de trás pra frente)
    date_idx = None
    for i in range(len(parts) - 1, -1, -1):
        if RE_DATE_TOKEN.fullmatch(parts[i]):
            date_idx = i
            break
    if date_idx is None:
        return None

    data_token = parts[date_idx]

    compoe_token = ""
    if date_idx + 1 < len(parts):
        compoe_token = parts[date_idx + 1]

    # normaliza Compõe para "Sim"/"Não" quando possível
    comp_norm = clean_spaces(compoe_token).strip().lower().strip(".")
    if comp_norm in ("sim", "s"):
        compoe_token = "Sim"
    elif comp_norm in ("nao", "não", "n"):
        compoe_token = "Não"

    # preço pode vir como "R$ 1.234,56" (dois tokens) ou "1.234,56" (um token) ou "R$1.234,56"
    # vamos pegar token imediatamente antes da data
    if date_idx - 1 < 0:
        return None

    preco_token = parts[date_idx - 1]
    if preco_token.upper() == "R$" and date_idx - 2 >= 0:
        preco_token = parts[date_idx - 2]
        # nesse caso quantidade fica um antes
        qtd_idx = date_idx - 3
    else:
        # se veio "R$1.234,56"
        if preco_token.upper().startswith("R$"):
            preco_token = preco_token.replace("R$", "").strip()
        qtd_idx = date_idx - 2

    if qtd_idx < 0:
        qtd = None
    else:
        qtd_token = parts[qtd_idx]
        try:
            qtd = float(qtd_token.replace(".", "").replace(",", "."))
        except Exception:
            qtd = None

    try:
        num_int = int(num)
    except Exception:
        return None

    return {
        "Nº": num_int,
        "Inciso": inciso,
        "Quantidade": qtd,
        "Preço unitário": preco_token,
        "Data": data_token,
        "Compõe": compoe_token,
    }


# =========================================================
# Estatística / Regras de exclusão
# =========================================================

def media_sem_o_valor(vals: list[float], idx: int):
    if not vals or idx < 0 or idx >= len(vals):
        return None
    others = vals[:idx] + vals[idx + 1 :]
    if not others:
        return None
    return sum(others) / len(others)


def coeficiente_variacao(vals: list[float]):
    if not vals:
        return None
    mean = sum(vals) / len(vals)
    if mean == 0:
        return None
    var = sum((v - mean) ** 2 for v in vals) / len(vals)
    std = var ** 0.5
    return std / mean


def filtrar_outliers_por_ratio(vals: list[float], upper: float = 1.25, lower: float = 0.75):
    if len(vals) < 5:
        return vals, 0, 0

    keep_alto = []
    excl_alto = 0

    for i, v in enumerate(vals):
        m = media_sem_o_valor(vals, i)
        if m is None or m == 0:
            keep_alto.append(v)
            continue
        ratio = v / m
        if ratio > upper:
            excl_alto += 1
        else:
            keep_alto.append(v)

    keep_baixo = []
    excl_baixo = 0

    for i, v in enumerate(keep_alto):
        m = media_sem_o_valor(keep_alto, i)
        if m is None or m == 0:
            keep_baixo.append(v)
            continue
        ratio = v / m
        if ratio < lower:
            excl_baixo += 1
        else:
            keep_baixo.append(v)

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


# =========================================================
# Relatório por item (Preview, Excel, PDFs)
# =========================================================

def build_itens_relatorio(df: pd.DataFrame, payload: dict | None = None) -> list[dict]:
    """
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

    OBS: included_indices são índices relativos à lista 'valores_brutos' (que é retornada no preview).
    Nesta versão, 'valores_brutos' está ORDENADA (crescente) e 'fontes_brutas' alinhada.
    """
    payload = payload or {}
    last_quotes = payload.get("last_quotes") or {}
    manual_overrides = payload.get("manual_overrides") or {}

    if df is None or df.empty:
        return []

    if "Preço unitário" not in df.columns:
        raise ValueError("Coluna 'Preço unitário' não encontrada no dataframe.")

    itens: list[dict] = []

    for item, g_raw in df.groupby("Item", sort=False):
        catmat = ""
        if "CATMAT" in g_raw.columns and g_raw["CATMAT"].notna().any():
            catmat = str(g_raw["CATMAT"].dropna().iloc[0])

        n_bruto = int(len(g_raw))

        # Monta lista numérica + fonte por linha
        pairs = []
        for _, row in g_raw.iterrows():
            v = preco_txt_to_float(row.get("Preço unitário"))
            if v is None:
                continue
            fonte = str(row.get("Fonte") or "")
            pairs.append((float(v), fonte))

        # Ordena por valor (crescente) para facilitar análise e para bater com UI
        pairs.sort(key=lambda x: x[0])

        valores_brutos = [p[0] for p in pairs]
        fontes_brutas = [p[1] for p in pairs]

        # --------- cálculo automático
        excl_alto = 0
        excl_baixo = 0
        metodo_auto = ""
        valor_auto = None
        valores_finais_auto: list[float] = []
        cv_auto = None

        if len(valores_brutos) == 0:
            metodo_auto = ""
            valor_auto = None
            valores_finais_auto = []
            cv_auto = None
        elif len(valores_brutos) == 1:
            metodo_auto = "Valor único"
            valor_auto = valores_brutos[0]
            valores_finais_auto = valores_brutos[:]
            cv_auto = None
        elif len(valores_brutos) < 5:
            cvv = _cv(valores_brutos)
            meanv = _mean(valores_brutos)
            medv = _median(valores_brutos)
            if cvv is None:
                metodo_auto = "Mediana"
                valor_auto = medv
            else:
                if cvv < 0.25:
                    metodo_auto = "Média"
                    valor_auto = meanv
                else:
                    metodo_auto = "Mediana"
                    valor_auto = medv
            valores_finais_auto = valores_brutos[:]
            cv_auto = cvv
        else:
            vals_filtrados, excl_alto, excl_baixo = filtrar_outliers_por_ratio(valores_brutos, upper=1.25, lower=0.75)
            valores_finais_auto = vals_filtrados[:]
            valor_auto = _mean(valores_finais_auto) if valores_finais_auto else None
            metodo_auto = "Média"
            cv_auto = _cv(valores_finais_auto) if valores_finais_auto else None

        # --------- último licitado
        last_quote_val = last_quotes.get(item)
        last_quote = _safe_float(last_quote_val)

        # --------- regra nova: permitir ajuste quando valor_auto <= 1,2 * last_quote
        allow_manual = False
        if last_quote is not None and valor_auto is not None and last_quote > 0:
            allow_manual = (valor_auto <= 1.2 * last_quote)

        # --------- decisão final (auto vs manual)
        modo = "Auto"
        valor_final = valor_auto
        metodo_final = metodo_auto
        valores_finais = valores_finais_auto[:]
        manual_info = None

        ov = manual_overrides.get(item) if isinstance(manual_overrides, dict) else None
        if allow_manual and isinstance(ov, dict):
            included_indices = ov.get("included_indices")
            method = (ov.get("method") or "media").lower()

            if isinstance(included_indices, list) and len(included_indices) > 0:
                sel = []
                sel_fontes = []
                for idx in included_indices:
                    if isinstance(idx, int) and 0 <= idx < len(valores_brutos):
                        sel.append(valores_brutos[idx])
                        sel_fontes.append(fontes_brutas[idx])

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
        elif allow_manual and not (isinstance(ov, dict) and ov):
            modo = "Pendente"

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
                "valores_brutos": valores_brutos,          # ordenados
                "fontes_brutos": fontes_brutas,            # alinhados
                "valor_auto": valor_auto,
                "metodo_auto": metodo_auto,
                "n_final_auto": int(len(valores_finais_auto)),
                "excl_altos": int(excl_alto),
                "excl_baixos": int(excl_baixo),
                "cv_auto": cv_auto,
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


# =========================================================
# Resumo + Excel
# =========================================================

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
        catmat = g["CATMAT"].dropna().iloc[0] if ("CATMAT" in g.columns and g["CATMAT"].notna().any()) else ""
        vals = g["preco_num"].astype(float).tolist()
        n_inicial = len(vals)

        excl_alto = 0
        excl_baixo = 0

        if n_inicial < 5:
            cvv = coeficiente_variacao(vals)
            meanv = sum(vals) / len(vals) if vals else None
            medv = float(pd.Series(vals).median()) if vals else None

            if cvv is None:
                escolhido = "Mediana"
                valor = medv
            else:
                if cvv < 0.25:
                    escolhido = "Média"
                    valor = meanv
                else:
                    escolhido = "Mediana"
                    valor = medv

            n_final = n_inicial
            cv_final = cvv

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
    """
    Gera Excel (bytes) com:
    - Dados (linhas Compõe=Sim)
    - Resumo (cálculo automático atual)
    - Prévia (tabela comparativa + último licitado + modo final)
    """
    df_resumo = gerar_resumo(df)

    preview_rows = []
    for it in itens_relatorio:
        valor_auto = _safe_float(it.get("valor_auto"))
        lastq = _safe_float(it.get("last_quote"))
        valor_final = _safe_float(it.get("valor_final"))

        diff_abs = (valor_final - lastq) if (valor_final is not None and lastq is not None) else None
        diff_pct = ((diff_abs / lastq) * 100.0) if (diff_abs is not None and lastq not in (None, 0)) else None

        preview_rows.append(
            {
                "Item": it.get("item"),
                "Catmat": it.get("catmat"),
                "Número de entradas iniciais": it.get("n_bruto"),
                "Número de entradas finais": it.get("n_final_auto"),
                "Nº desconsiderados (Excessivamente Elevados)": it.get("excl_altos"),
                "Nº desconsiderados (Inexequíveis)": it.get("excl_baixos"),
                "Valor calculado (R$)": float_to_preco_txt(valor_auto, decimals=2),
                "Último licitado (R$)": float_to_preco_txt(lastq, decimals=2),
                "Modo final": it.get("modo_final"),
                "Método final": it.get("metodo_final"),
                "Valor final adotado (R$)": float_to_preco_txt(valor_final, decimals=2),
                "Diferença vs último (R$)": float_to_preco_txt(diff_abs, decimals=2),
                "Diferença vs último (%)": (f"{diff_pct:.2f}%".replace(".", ",") if diff_pct is not None else ""),
            }
        )

    df_preview = pd.DataFrame(preview_rows)

    df_to_write = df if df is not None else pd.DataFrame()

    excel_out = io.BytesIO()
    with pd.ExcelWriter(excel_out, engine="openpyxl") as writer:
        df_to_write.to_excel(writer, index=False, sheet_name="Dados")
        df_resumo.to_excel(writer, index=False, sheet_name="Resumo")
        df_preview.to_excel(writer, index=False, sheet_name="Prévia")

    excel_out.seek(0)
    return excel_out.read()


# =========================================================
# Parser do PDF (extração)
# =========================================================

def process_pdf_bytes_debug(pdf_bytes: bytes) -> tuple[pd.DataFrame, list[dict]]:
    # ✅ valida o tipo do relatório antes de qualquer parsing
    _detect_report_type_or_raise(pdf_bytes)

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
    return df


def validate_extraction(df: pd.DataFrame) -> dict:
    return {"total_rows": int(len(df)) if df is not None else 0}


def debug_dump(df: pd.DataFrame, debug_records: list[dict], max_rows: int = 200) -> str:
    out = []
    out.append("=" * 120)
    out.append("DEBUG DUMP — REGISTROS EXTRAÍDOS (com Fonte)")
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


# =========================================================
# Memória de Cálculo (PDF)
# =========================================================

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
    """Replica o padrão do debug para um único item."""
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
    """
    Texto da memória (com marcadores):
      - <<TITLE>>...<<ENDTITLE>> : título (fonte maior, negrito)
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
        parts = [p.strip() for p in (s or "").split("||")]
        return [p for p in parts if p != ""]

    def _cv_pct_txt(cv: float | None) -> str:
        if cv is None:
            return ""
        pct = cv * 100.0
        s = f"{pct:.2f}".replace(".", ",")
        return f"{s}%"

    payload = payload or {}
    relatorio = build_itens_relatorio(df, payload=payload) if df is not None else []
    rel_map = {str(r.get("item")): r for r in relatorio}

    out: list[str] = []

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

        out.append("<<B>>ANÁLISE MANUAL<<ENDB>>")
        out.append("Valores brutos (numéricos) disponíveis (ordenados):")
        vals = r.get("valores_brutos") or []
        fontes = r.get("fontes_brutas") or []

        # índice 1-based
        for i, v in enumerate(vals, start=1):
            fonte = fontes[i - 1] if (i - 1) < len(fontes) else ""
            out.append(f"[{i}] { _fmt_num_cond(v) } | Fonte: {fonte}")

        out.append("")
        inc = manual.get("included_indices") or []
        # indices são 0-based internamente; exibir 1-based
        inc_1b = [(i + 1) for i in inc if isinstance(i, int)]
        out.append(f"Índices incluídos: {inc_1b}")
        out.append(f"Quantidade excluída manualmente: {manual.get('excluded_count', '')}")
        out.append(f"Método escolhido: {manual.get('method', '')}")

        meanv = _safe_float(manual.get("mean"))
        medianv = _safe_float(manual.get("median"))
        cvv = _safe_float(manual.get("cv"))

        out.append(f"Média (dos incluídos): {_fmt_num_cond(meanv) if meanv is not None else ''}")
        out.append(f"Mediana (dos incluídos): {_fmt_num_cond(medianv) if medianv is not None else ''}")
        out.append(f"Coeficiente de Variação (dos incluídos): {_cv_pct_txt(cvv)}")
        out.append(f"Valor Final (manual): {_fmt_brl_cond(_safe_float(manual.get('valor_final')))}")

        just_code = (manual.get("justificativa_codigo") or "").strip()
        just_txt = (manual.get("justificativa_texto") or "").strip()
        if just_code:
            out.append(f"Justificativa (código): {just_code}")
        if just_txt:
            out.append(f"Justificativa (texto): {just_txt}")
        out.append("")

    # Cabeçalho / metodologia (conforme sua memória)
    out.append("<<TITLE>>MEMÓRIA DE CÁLCULO - TABELA COMPARATIVA DE VALORES<<ENDTITLE>>")
    out.append("<<TITLE>>UPDE - HUSM - UFSM<<ENDTITLE>>")
    out.append("")

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

    # Por item
    for item, g_raw in df.groupby("Item", sort=False):
        out.append(f"<<B>>{'_' * 50}<<ENDB>>")
        out.append(f"<<B>>{str(item)}<<ENDB>>")
        out.append(f"Amostrar Iniciais: {len(g_raw)}")

        # valores numéricos já ordenados (do relatorio)
        r = rel_map.get(str(item))
        vals = (r.get("valores_brutos") if r else []) or []

        if len(vals) == 0:
            out.append("Nenhum valor conseguiu ser convertido para número.")
            out.append('Valores originais da coluna "Preço Unitário" (primeiros 50):')
            out.append(", ".join([str(x) for x in g_raw["Preço unitário"].tolist()[:50]]))
            out.append("")
            _append_last_and_final(str(item))
            _append_manual_section(str(item))
            continue

        if len(vals) == 1:
            out.append(f"Valor único: {_fmt_num_cond(vals[0])}")
            out.append("Preço Final Escolhido: Valor único.")
            out.append(f"Valor escolhido: {float_to_preco_txt(vals[0], decimals=2)}")
            out.append("")
            _append_last_and_final(str(item))
            _append_manual_section(str(item))
            continue

        if len(vals) < 5:
            cvv = _coef_var(vals)
            meanv = _mean(vals)
            medv = _median(vals)

            out.append("Valores Iniciais considerados no cálculo:")
            # índice 1-based
            for i, v in enumerate(vals, start=1):
                out.append(f"[{i}] {_fmt_num_cond(v)}")
            out.append("")

            out.append(f"Média: {_fmt_num_cond(meanv)}")
            out.append(f"Mediana: {_fmt_num_cond(medv)}")
            out.append(f"CV: {_cv_pct_txt(cvv)}")

            if cvv is None:
                escolhido = "Mediana"
                valor = medv
                motivo = "CV indefinido (média=0)"
            elif cvv < 0.25:
                escolhido = "Média"
                valor = meanv
                motivo = "CV < 0,25"
            else:
                escolhido = "Mediana"
                valor = medv
                motivo = "CV >= 0,25"

            out.append(f"Decisão: {escolhido} ({motivo})")
            out.append(f"Valor Final: {_fmt_brl_cond(valor)}")
            out.append("")
            _append_last_and_final(str(item))
            _append_manual_section(str(item))
            continue

        rep = _audit_item(vals, upper=1.25, lower=0.75)

        out.append("Valores Iniciais considerados no cálculo:")
        for i, v in enumerate(rep["iniciais"], start=1):
            out.append(f"[{i}] {_fmt_num_cond(v)}")
        out.append("")

        out.append("--- Preços exclúidos por serem Excessivamente Elevados ---")
        out.append(f"Quantidade: {len(rep['excluidos_altos'])}")
        for r2 in rep["excluidos_altos"]:
            out.append(
                f"Valor={_fmt_num_cond(r2['v'])} | Média dos demais={_fmt_num_cond(r2['m_outros'])} | Proporção={r2['ratio']:.4f}"
            )
        out.append("")

        out.append("Mantidos após exclusão dos Excessivamente Elevados:")
        out.append(", ".join([_fmt_num_cond(v) for v in rep["apos_alto"]]))
        out.append("")

        out.append("--- Preços exclúidos por serem Inexequíveis ---")
        out.append(f"Quantidade: {len(rep['excluidos_baixos'])}")
        for r2 in rep["excluidos_baixos"]:
            out.append(
                f"Valor={_fmt_num_cond(r2['v'])} | Média dos demais={_fmt_num_cond(r2['m_outros'])} | Proporção={r2['ratio']:.4f}"
            )
        out.append("")

        out.append("Valores considerados no cálculo final:")
        out.append(", ".join([_fmt_num_cond(v) for v in rep["finais"]]))
        out.append(f"Número de valores considerados no cálculo final: {len(rep['finais'])}")
        out.append(f"Média final: {_fmt_num_cond(_safe_float(rep['media_final']))}")
        out.append(f"Coeficiente de Variação final: {_cv_pct_txt(_safe_float(rep['cv_final']))}")
        out.append("Decisão Final: Média")
        out.append(f"Valor Final: {_fmt_brl_cond(_safe_float(rep['media_final']))}")
        out.append("")
        _append_last_and_final(str(item))
        _append_manual_section(str(item))

    return "\n".join(out) + "\n"


def _text_to_pdf_bytes(text: str) -> bytes:
    """
    Renderiza o TXT (com marcadores simples) em PDF com quebra de página.
    Mantém aparência geral (Courier), com título maior e negrito.
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
            y0 = y - 2
            y1 = y + curr_font_size + 2
            c.linkURL(link_url, (left, y0, left + w, y1), relative=0)

        y -= curr_line_height

    def _strip_marker(line: str):
        if line.startswith("<<TITLE>>") and line.endswith("<<ENDTITLE>>"):
            return ("TITLE", line[len("<<TITLE>>") : -len("<<ENDTITLE>>")], None)
        if line.startswith("<<B>>") and line.endswith("<<ENDB>>"):
            return ("B", line[len("<<B>>") : -len("<<ENDB>>")], None)
        if line.startswith("<<LINK|") and line.endswith("<<ENDLINK>>"):
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
    txt = build_memoria_calculo_txt(df, payload=payload)
    return _text_to_pdf_bytes(txt)


# =========================================================
# PDF: Tabela comparativa
# =========================================================

def build_pdf_tabela_comparativa_bytes(itens_relatorio: list[dict]) -> bytes:
    """
    Gera o PDF 'Tabela Comparativa de Valores' (bytes).
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
    story.append(Paragraph("TABELA COMPARATIVA DE VALORES — UPDE / HUSM / UFSM", style_title))
    story.append(Spacer(1, 8))
    story.append(Paragraph("Resumo por item (inclui último licitado e modo final quando houver ajuste manual).", style_normal))
    story.append(Spacer(1, 12))

    headers = [
        "Item",
        "Catmat",
        "N iniciais",
        "N finais",
        "Excl. altos",
        "Excl. inexeq.",
        "Valor calc.",
        "Último licitado",
        "Modo",
        "Método",
        "Valor final",
        "Dif. (R$)",
    ]

    data = [headers]
    for it in itens_relatorio:
        valor_auto = _safe_float(it.get("valor_auto"))
        lastq = _safe_float(it.get("last_quote"))
        valor_final = _safe_float(it.get("valor_final"))
        diff_abs = (valor_final - lastq) if (valor_final is not None and lastq is not None) else None

        data.append(
            [
                str(it.get("item", "")),
                str(it.get("catmat", "")),
                str(it.get("n_bruto", "")),
                str(it.get("n_final_auto", "")),
                str(it.get("excl_altos", "")),
                str(it.get("excl_baixos", "")),
                float_to_preco_txt(valor_auto, decimals=2),
                float_to_preco_txt(lastq, decimals=2),
                str(it.get("modo_final", "")),
                str(it.get("metodo_final", "")),
                float_to_preco_txt(valor_final, decimals=2),
                float_to_preco_txt(diff_abs, decimals=2),
            ]
        )

    t = Table(data, repeatRows=1)
    t.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 9),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
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
            "Obs.: Itens em modo 'Pendente' indicam que o ajuste manual está liberado, mas não foi aplicado.",
            style_normal,
        )
    )

    doc.build(story)
    buf.seek(0)
    return buf.read()
