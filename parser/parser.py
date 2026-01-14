import re
import io
import pdfplumber
import pandas as pd


RE_ITEM = re.compile(r"^Item:\s*(\d+)\b", re.IGNORECASE)
RE_CATMAT = re.compile(r"(\d{6})\s*-\s*")

RE_PAGE_MARK = re.compile(r"^\s*\d+\s+de\s+\d+\s*$", re.IGNORECASE)

RE_DATE_TOKEN = re.compile(r"^\d{2}/\d{2}/\d{4}$")
RE_ROW_START = re.compile(r"^\s*(\d+)\s+([IVX]+)\s+", re.IGNORECASE)

RE_ONLY_NO = re.compile(r"^\s*(\d+)\s*$")
RE_ONLY_NO_INCISO = re.compile(r"^\s*(\d+)\s+([IVX]+)\s*$", re.IGNORECASE)
RE_ONLY_INCISO_REST = re.compile(r"^\s*([IVX]+)\s+.+", re.IGNORECASE)

FINAL_COLUMNS = [
    "Item",
    "CATMAT",
    "Nº",
    "Inciso",
    "Nome",
    "Quantidade",
    "Preço unitário",
    "Data",
    "Compõe",
]


def clean_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def normalize_text(s: str) -> str:
    """
    Normalizações importantes para o Compras.gov.br:
    - Junta "gov." + "br" => "gov.br"
    - Separa "110Unidade" => "110 Unidade"
    - Normaliza "R$   150,4500" => "R$ 150,4500"
    """
    s = s.replace("\u00a0", " ")
    s = clean_spaces(s)

    # gov. br -> gov.br
    s = re.sub(r"(gov\.)\s*(br)\b", r"\1\2", s, flags=re.IGNORECASE)

    # 110Unidade -> 110 Unidade
    s = re.sub(r"(\d)([A-Za-zÀ-ÿ])", r"\1 \2", s)
    s = re.sub(r"([A-Za-zÀ-ÿ])(\d)", r"\1 \2", s)

    # R$ com espaços
    s = re.sub(r"R\$\s+", "R$ ", s)

    return s


def record_is_complete(s: str) -> bool:
    """
    Considera registro completo quando:
    - contém R$
    - contém uma data dd/mm/aaaa
    - termina com Sim/Não
    """
    s = normalize_text(s)
    if not s:
        return False
    if not ("R$" in s):
        return False
    if not any(RE_DATE_TOKEN.fullmatch(t) for t in s.split(" ")):
        return False
    if not (s.endswith("Sim") or s.endswith("Não")):
        return False
    return True


def parse_record(record: str):
    """
    Parse de um registro lógico:
    Nº Inciso Nome... Quantidade Unidade R$ Valor Data Sim/Não

    Retorna preço cru (sem 'R$').
    """
    s = normalize_text(record)
    toks = s.split(" ")

    if len(toks) < 8:
        return None
    if not toks[0].isdigit():
        return None
    if not re.fullmatch(r"[IVX]+", toks[1], flags=re.IGNORECASE):
        return None

    no = toks[0]
    inciso = toks[1]
    compoe = toks[-1]
    if compoe not in ("Sim", "Não"):
        return None

    # Data: normalmente penúltimo; fallback: última data
    data = toks[-2]
    if not RE_DATE_TOKEN.fullmatch(data):
        dates = [t for t in toks if RE_DATE_TOKEN.fullmatch(t)]
        if not dates:
            return None
        data = dates[-1]

    # Localizar R$
    r_idx = None
    for i, t in enumerate(toks):
        if t == "R$" or t.startswith("R$"):
            r_idx = i
            break
    if r_idx is None:
        return None

    # Preço cru
    if toks[r_idx] == "R$":
        if r_idx + 1 >= len(toks):
            return None
        preco_raw = toks[r_idx + 1]
    else:
        preco_raw = toks[r_idx].replace("R$", "").strip()
    if not preco_raw:
        return None

    # Quantidade: preferir "<num> Unidade"
    qtd_idx = None
    for i in range(2, len(toks) - 1):
        if re.fullmatch(r"\d+(?:[.,]\d+)?", toks[i]) and toks[i + 1].lower().startswith("unidade"):
            qtd_idx = i
            break

    # Fallback: último número antes do R$
    if qtd_idx is None:
        for j in range(r_idx - 1, 1, -1):
            if re.fullmatch(r"\d+(?:[.,]\d+)?", toks[j]):
                qtd_idx = j
                break

    if qtd_idx is None:
        return None

    quantidade = toks[qtd_idx]
    nome = clean_spaces(" ".join(toks[2:qtd_idx]))

    return {
        "Nº": no,
        "Inciso": inciso,
        "Nome": nome,
        "Quantidade": quantidade,
        "Preço unitário": preco_raw,
        "Data": data,
        "Compõe": compoe,
    }


def process_pdf_bytes(pdf_bytes: bytes) -> pd.DataFrame:
    records = []

    current_item = None
    current_catmat = None

    # Em vez de depender de header, usamos:
    # captura ativa após "Período:" e desativa em "Legenda:" ou próximo Item.
    capture_table = False

    current_row = None
    row_item = None
    row_catmat = None

    pending_no = None
    pending_no_inciso = None

    def flush_row():
        nonlocal current_row, row_item, row_catmat
        if current_row and record_is_complete(current_row):
            parsed = parse_record(current_row)
            if parsed:
                records.append({
                    "Item": f"Item {row_item}" if row_item is not None else None,
                    "CATMAT": row_catmat,
                    **parsed
                })
        current_row = None
        row_item = None
        row_catmat = None

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

                # Novo item
                m_item = RE_ITEM.match(line)
                if m_item:
                    flush_row()
                    pending_no = None
                    pending_no_inciso = None
                    capture_table = False

                    current_item = int(m_item.group(1))
                    current_catmat = None
                    continue

                # CATMAT
                m_cat = RE_CATMAT.search(line)
                if m_cat:
                    current_catmat = m_cat.group(1)

                line_norm = normalize_text(line)

                # Liga captura quando encontra o "Período:" (antes da tabela)
                if "Período:" in line_norm or "Periodo:" in line_norm:
                    flush_row()
                    pending_no = None
                    pending_no_inciso = None
                    capture_table = True
                    continue

                # Desliga captura ao chegar na legenda
                if line_norm.lower().startswith("legenda"):
                    flush_row()
                    pending_no = None
                    pending_no_inciso = None
                    capture_table = False
                    continue

                if not capture_table:
                    continue

                # ==== A partir daqui, estamos “dentro” da tabela do item ====

                # Caso 1: linha só com Nº
                m_only_no = RE_ONLY_NO.match(line_norm)
                if m_only_no:
                    flush_row()
                    pending_no = m_only_no.group(1)
                    pending_no_inciso = None
                    continue

                # Caso 2: linha só com "Nº Inciso"
                m_only_no_inc = RE_ONLY_NO_INCISO.match(line_norm)
                if m_only_no_inc:
                    flush_row()
                    pending_no = None
                    pending_no_inciso = f"{m_only_no_inc.group(1)} {m_only_no_inc.group(2)}"
                    continue

                # Caso 3: linha normal começando com "Nº Inciso ..."
                if RE_ROW_START.match(line_norm):
                    flush_row()
                    current_row = line_norm
                    row_item = current_item
                    row_catmat = current_catmat
                    pending_no = None
                    pending_no_inciso = None

                    if record_is_complete(current_row):
                        flush_row()
                    continue

                # Caso 4: temos pending_no e a linha começa com Inciso + resto
                if pending_no and RE_ONLY_INCISO_REST.match(line_norm):
                    flush_row()
                    current_row = f"{pending_no} {line_norm}"
                    row_item = current_item
                    row_catmat = current_catmat
                    pending_no = None
                    pending_no_inciso = None

                    if record_is_complete(current_row):
                        flush_row()
                    continue

                # Caso 5: temos pending_no_inciso e a linha é o resto
                if pending_no_inciso:
                    flush_row()
                    current_row = f"{pending_no_inciso} {line_norm}"
                    row_item = current_item
                    row_catmat = current_catmat
                    pending_no = None
                    pending_no_inciso = None

                    if record_is_complete(current_row):
                        flush_row()
                    continue

                # Continuação de registro
                if current_row:
                    current_row = clean_spaces(current_row + " " + line_norm)
                    if record_is_complete(current_row):
                        flush_row()
                else:
                    # linha solta dentro da tabela sem registro iniciado
                    continue

    # Flush final
    flush_row()

    df = pd.DataFrame(records, columns=FINAL_COLUMNS)

    # Somente Compõe=Sim
    if "Compõe" in df.columns:
        df = df[df["Compõe"] == "Sim"].copy()

    df.reset_index(drop=True, inplace=True)

    for col in FINAL_COLUMNS:
        if col not in df.columns:
            df[col] = None
    df = df[FINAL_COLUMNS]

    return df


def validate_extraction(df: pd.DataFrame) -> dict:
    total = int(len(df))
    if total == 0:
        return {"total_rows": 0, "rows_nome_vazio": 0, "pct_nome_vazio": 0.0}

    nome_series = df["Nome"].fillna("").astype(str).str.strip()
    rows_nome_vazio = int((nome_series == "").sum())
    pct_nome_vazio = round((rows_nome_vazio / total) * 100, 2)

    return {"total_rows": total, "rows_nome_vazio": rows_nome_vazio, "pct_nome_vazio": pct_nome_vazio}
