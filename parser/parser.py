import re
import io
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
            "PDF incorreto: você carregou o Relatório Detalhado. "
            "Por favor, gere e envie a versão Relatório Resumido."
        )

    raise PdfIncompatibilityError(
        "PDF incompatível: não foi possível identificar Relatório Resumido nem Relatório Detalhado "
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
    return (("nº" in line) or ("no" in l)) and ("inciso" in l) and ("quantidade" in l)


def is_table_off(line: str) -> bool:
    l = (line or "").strip().lower()
    # gatilhos conservadores
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


def _fmt_num_cond(v):
    """
    Se valor >= 1, usar 2 casas; se < 1, usar 4 casas.
    """
    if v is None:
        return ""
    try:
        v = float(v)
    except Exception:
        return ""
    decimals = 2 if abs(v) >= 1 else 4
    return f"{v:.{decimals}f}"


def _fmt_brl_cond(v):
    """
    BRL com 2 casas quando >=1, 4 casas quando <1.
    """
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
    if date_idx - 1 < 0:
        return None

    preco_token = parts[date_idx - 1]
    if preco_token.upper() == "R$" and date_idx - 2 >= 0:
        preco_token = parts[date_idx - 2]
        qtd_idx = date_idx - 3
    else:
        if preco_token.upper().startswith("R$"):
            preco_token = preco_token.replace("R$", "").strip()
        qtd_idx = date_idx - 2

    qtd = None
    if qtd_idx >= 0:
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


def _mean(vals: list[float]) -> float | None:
    if not vals:
        return None
    return sum(vals) / len(vals)


def _median(vals: list[float]) -> float | None:
    if not vals:
        return None
    return float(pd.Series(vals).median())


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


# =========================================================
# Parser do PDF (extração)
# =========================================================

def process_pdf_bytes_debug(pdf_bytes: bytes) -> tuple[pd.DataFrame, list[dict]]:
    # valida o tipo do relatório antes de qualquer parsing
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


# =========================================================
# Relatório por item (Preview, Excel, PDFs)
# =========================================================

def build_itens_relatorio(df: pd.DataFrame, payload: dict | None = None) -> list[dict]:
    """
    Retorna lista de dict por item, incluindo:
      - brutos: lista de objetos [{idx, valor, fonte}] na ORDEM ORIGINAL DO DF (estável)
    O front ordena apenas para exibição no modal.

    payload (opcional):
      - last_quotes: {"Item 1": 123.45, ...}
      - manual_overrides: {"Item 1": {"included_indices":[0,2], "method":"media|mediana", ...}}
    """
    payload = payload or {}
    last_quotes = payload.get("last_quotes") or {}
    manual_overrides = payload.get("manual_overrides") or {}

    if df is None or df.empty:
        return []

    itens: list[dict] = []

    for item, g_raw in df.groupby("Item", sort=False):
        catmat = ""
        if "CATMAT" in g_raw.columns and g_raw["CATMAT"].notna().any():
            catmat = str(g_raw["CATMAT"].dropna().iloc[0])

        n_bruto = int(len(g_raw))

        # BRUTOS em ordem original do DF (índice estável)
        brutos = []
        for idx0, (_, row) in enumerate(g_raw.iterrows()):
            v = preco_txt_to_float(row.get("Preço unitário"))
            if v is None:
                continue
            fonte = str(row.get("Fonte") or "")
            brutos.append({"idx": idx0, "valor": float(v), "fonte": fonte})

        valores = [b["valor"] for b in brutos]

        excl_alto = 0
        excl_baixo = 0
        metodo_auto = ""
        valor_auto = None
        valores_finais_auto: list[float] = []
        cv_auto = None

        if len(valores) == 0:
            metodo_auto = ""
            valor_auto = None
            valores_finais_auto = []
            cv_auto = None
        elif len(valores) == 1:
            metodo_auto = "Valor único"
            valor_auto = valores[0]
            valores_finais_auto = valores[:]
            cv_auto = None
        elif len(valores) < 5:
            cvv = _cv(valores)
            meanv = _mean(valores)
            medv = _median(valores)
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
            valores_finais_auto = valores[:]
            cv_auto = cvv
        else:
            vals_filtrados, excl_alto, excl_baixo = filtrar_outliers_por_ratio(valores, upper=1.25, lower=0.75)
            valores_finais_auto = vals_filtrados[:]
            valor_auto = _mean(valores_finais_auto) if valores_finais_auto else None
            metodo_auto = "Média"
            cv_auto = _cv(valores_finais_auto) if valores_finais_auto else None

        last_quote_val = last_quotes.get(item)
        last_quote = _safe_float(last_quote_val)

        allow_manual = False
        if last_quote is not None and valor_auto is not None and last_quote > 0:
            allow_manual = (valor_auto <= 1.2 * last_quote)

        modo = "Auto"
        valor_final = valor_auto
        metodo_final = metodo_auto
        cv_final = _cv(valores_finais_auto) if valores_finais_auto else None
        manual_info = None

        ov = manual_overrides.get(item) if isinstance(manual_overrides, dict) else None
        if allow_manual and isinstance(ov, dict):
            included_indices = ov.get("included_indices") or []
            method = (ov.get("method") or "media").lower()

            sel = []
            # included_indices referenciam idx0 original (da lista brutos)
            allowed_set = set([i for i in included_indices if isinstance(i, int)])

            for b in brutos:
                if b["idx"] in allowed_set:
                    sel.append(b["valor"])

            if len(sel) > 0:
                modo = "Manual"
                if method in ("mediana", "median"):
                    metodo_final = "Mediana"
                    valor_final = _median(sel)
                else:
                    metodo_final = "Média"
                    valor_final = _mean(sel)

                manual_info = {
                    "included_indices": list(allowed_set),
                    "excluded_count": int(len(valores) - len(sel)),
                    "method": metodo_final,
                    "valor_final": valor_final,
                    "cv": _cv(sel),
                    "mean": _mean(sel),
                    "median": _median(sel),
                    "justificativa_codigo": ov.get("justificativa_codigo") or "",
                    "justificativa_texto": ov.get("justificativa_texto") or "",
                }
                cv_final = _cv(sel)

        elif allow_manual and not (isinstance(ov, dict) and ov):
            modo = "Pendente"

        comparacao = ""
        diff_abs = None
        if last_quote is not None and valor_final is not None:
            if valor_final > last_quote:
                comparacao = "Maior"
            elif valor_final < last_quote:
                comparacao = "Menor"
            else:
                comparacao = "Igual"
            diff_abs = valor_final - last_quote

        itens.append(
            {
                "item": item,
                "catmat": catmat,
                "n_bruto": n_bruto,
                "brutos": brutos,  # <<< ESSENCIAL PARA O MODAL (valor + fonte + idx)
                "valor_auto": valor_auto,
                "metodo_auto": metodo_auto,
                "n_final_auto": int(len(valores_finais_auto)),
                "excl_altos": int(excl_alto),
                "excl_baixos": int(excl_baixo),
                "cv_auto": cv_auto,
                "allow_manual": bool(allow_manual),
                "modo_final": modo,
                "metodo_final": metodo_final,
                "valor_final": valor_final,
                "cv_final": cv_final,
                "last_quote": last_quote,
                "comparacao": comparacao,
                "diff_abs": diff_abs,
                "manual": manual_info,
            }
        )

    return itens


# =========================================================
# Excel (mantém enquanto você quiser)
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
            cvv = _cv(vals)
            meanv = _mean(vals)
            medv = _median(vals)

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
            valor = _mean(vals_filtrados) if n_final > 0 else None
            escolhido = "Média"
            cv_final = _cv(vals_filtrados) if n_final > 0 else None

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
# Memória de Cálculo (PDF) + Tabela Comparativa (PDF)
# (mantive as assinaturas; o restante do seu arquivo original pode estar igual)
# =========================================================

def build_memoria_calculo_txt(df: pd.DataFrame, payload: dict | None = None) -> str:
    # Mantém sua versão atual (com título, regras, hyperlink etc.)
    # >>> Para não sobrescrever suas personalizações, mantenha aqui o que você já tinha.
    # Se você quiser, eu também te devolvo a sua versão completa de memória
    # incorporando brutos+fonte manual, mas o problema do modal é resolvido só com preview+brutos.
    return "Memória de cálculo: mantenha sua implementação atual aqui.\n"


def _text_to_pdf_bytes(text: str) -> bytes:
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    left, right, top, bottom = 36, 36, 36, 36
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

    def _page_break(curr_font_name: str, curr_font_size: int):
        nonlocal y
        if y <= bottom:
            c.showPage()
            c.setFont(curr_font_name, curr_font_size)
            y = height - top

    def _draw(s: str, curr_font_name: str, curr_font_size: int, curr_lh: int):
        nonlocal y
        _page_break(curr_font_name, curr_font_size)
        c.setFont(curr_font_name, curr_font_size)
        c.drawString(left, y, s)
        y -= curr_lh

    for raw in (text or "").splitlines():
        line = raw.rstrip("\n")
        if len(line) <= max_chars:
            _draw(line, font_name, font_size, line_height)
        else:
            start = 0
            while start < len(line):
                chunk = line[start : start + max_chars]
                _draw(chunk, font_name, font_size, line_height)
                start += max_chars

    c.save()
    buffer.seek(0)
    return buffer.read()


def build_memoria_calculo_pdf_bytes(df: pd.DataFrame, payload: dict | None = None) -> bytes:
    txt = build_memoria_calculo_txt(df, payload=payload)
    return _text_to_pdf_bytes(txt)


def build_pdf_tabela_comparativa_bytes(itens_relatorio: list[dict]) -> bytes:
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
    doc.build(story)
    buf.seek(0)
    return buf.read()
