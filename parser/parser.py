import re
import io
import pdfplumber
import pandas as pd


RE_ITEM = re.compile(r"^Item:\s*(\d+)\b")
RE_CATMAT = re.compile(r"(\d{6})\s*-\s*")

RE_DATE_TOKEN = re.compile(r"^\d{2}/\d{2}/\d{4}$")
RE_PAGE_MARK = re.compile(r"^\s*\d+\s+de\s+\d+\s*$", re.IGNORECASE)

# Início "ideal" do registro
RE_ROW_START = re.compile(r"^\s*(\d+)\s+([IVX]+)\s+", re.IGNORECASE)
# Linhas quebradas do começo do registro
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
    Normaliza padrões típicos do texto extraído:
    - Junta "gov." + "br" => "gov.br"
    - Separa "110Unidade" => "110 Unidade"
    - Normaliza "R$   150,4500" => "R$ 150,4500"
    """
    s = s.replace("\u00a0", " ")
    s = clean_spaces(s)

    s = re.sub(r"(gov\.)\s*(br)\b", r"\1\2", s, flags=re.IGNORECASE)

    s = re.sub(r"(\d)([A-Za-zÀ-ÿ])", r"\1 \2", s)
    s = re.sub(r"([A-Za-zÀ-ÿ])(\d)", r"\1 \2", s)

    s = re.sub(r"R\$\s+", "R$ ", s)

    return s


def is_header_line(line: str) -> bool:
    """
    Header de tabela no Compras.gov.br varia MUITO (espaços somem, quebra de linha etc.).
    Então detectamos por “palavras-chave” ao invés de startswith.
    """
    s = normalize_text(line).lower()
    # precisa ter “inciso” e “compõe” e algum indicativo de preço
    return ("inciso" in s) and ("comp" in s) and ("pre" in s) and ("quant" in s)


def parse_record(record: str):
    """
    Espera algo como:
    Nº Inciso Nome... Quantidade Unidade R$ Valor Data Sim/Não
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

    # data: normalmente penúltimo token
    data = toks[-2]
    if not RE_DATE_TOKEN.fullmatch(data):
        dates = [t for t in toks if RE_DATE_TOKEN.fullmatch(t)]
        if not dates:
            return None
        data = dates[-1]

    # localizar R$
    r_idx = None
    for i, t in enumerate(toks):
        if t == "R$" or t.startswith("R$"):
            r_idx = i
            break
    if r_idx is None:
        return None

    # preço cru (sem R$)
    if toks[r_idx] == "R$":
        if r_idx + 1 >= len(toks):
            return None
        preco_raw = toks[r_idx + 1]
    else:
        preco_raw = toks[r_idx].replace("R$", "").strip()
    if not preco_raw:
        return None

    # quantidade: preferir "<num> Unidade"
    qtd_idx = None
    for i in range(2, len(toks) - 1):
        if re.fullmatch(r"\d+(?:[.,]\d+)?", toks[i]) and toks[i + 1].lower().startswith("unidade"):
            qtd_idx = i
            break

    # fallback: último número antes do R$
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
    in_table = False

    current_row = None
    current_row_item = None
    current_row_catmat = None

    # Para reconstruir início quebrado
    pending_no = None          # quando vier só "4"
    pending_no_inciso = None   # quando vier só "4 I"

    def flush_current_row():
        nonlocal current_row, current_row_item, current_row_catmat
        if current_row:
            parsed = parse_record(current_row)
            if parsed:
                records.append({
                    "Item": f"Item {current_row_item}" if current_row_item is not None else None,
                    "CATMAT": current_row_catmat,
                    **parsed
                })
        current_row = None
        current_row_item = None
        current_row_catmat = None

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

                # Item
                m_item = RE_ITEM.match(line)
                if m_item:
                    flush_current_row()
                    pending_no = None
                    pending_no_inciso = None
                    current_item = int(m_item.group(1))
                    current_catmat = None
                    in_table = False
                    continue

                # CATMAT
                m_cat = RE_CATMAT.search(line)
                if m_cat:
                    current_catmat = m_cat.group(1)

                # Header
                if is_header_line(line):
                    flush_current_row()
                    pending_no = None
                    pending_no_inciso = None
                    in_table = True
                    continue

                if not in_table:
                    continue

                # Normaliza
                line_norm = normalize_text(line)

                # 1) Se a linha é só um número (Nº sozinho), guardamos
                m_only_no = RE_ONLY_NO.match(line_norm)
                if m_only_no:
                    # Se já havia uma row em andamento, isso pode ser início do próximo registro
                    flush_current_row()
                    pending_no = m_only_no.group(1)
                    pending_no_inciso = None
                    continue

                # 2) Se a linha é "Nº Inciso" sozinho, guardamos
                m_only_no_inc = RE_ONLY_NO_INCISO.match(line_norm)
                if m_only_no_inc:
                    flush_current_row()
                    pending_no = None
                    pending_no_inciso = f"{m_only_no_inc.group(1)} {m_only_no_inc.group(2)}"
                    continue

                # 3) Início "ideal" do registro (Nº Inciso + resto)
                if RE_ROW_START.match(line_norm):
                    flush_current_row()
                    current_row = line_norm
                    current_row_item = current_item
                    current_row_catmat = current_catmat
                    pending_no = None
                    pending_no_inciso = None
                    continue

                # 4) Se veio "I ..." e antes veio só "4" (Nº separado), juntamos
                if pending_no and RE_ONLY_INCISO_REST.match(line_norm):
                    flush_current_row()
                    current_row = f"{pending_no} {line_norm}"
                    current_row_item = current_item
                    current_row_catmat = current_catmat
                    pending_no = None
                    pending_no_inciso = None
                    continue

                # 5) Se antes veio "4 I" e agora vem o resto, juntamos
                if pending_no_inciso:
                    flush_current_row()
                    current_row = f"{pending_no_inciso} {line_norm}"
                    current_row_item = current_item
                    current_row_catmat = current_catmat
                    pending_no = None
                    pending_no_inciso = None
                    continue

                # 6) Continuação do registro atual
                if current_row:
                    current_row = clean_spaces(current_row + " " + line_norm)
                else:
                    # linha “solta” dentro da tabela sem registro iniciado: ignora
                    continue

    # flush final
    flush_current_row()

    df = pd.DataFrame(records, columns=FINAL_COLUMNS)

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
        return {
            "total_rows": 0,
            "rows_nome_vazio": 0,
            "pct_nome_vazio": 0.0,
        }

    nome_series = df["Nome"].fillna("").astype(str).str.strip()
    rows_nome_vazio = int((nome_series == "").sum())
    pct_nome_vazio = round((rows_nome_vazio / total) * 100, 2)

    return {
        "total_rows": total,
        "rows_nome_vazio": rows_nome_vazio,
        "pct_nome_vazio": pct_nome_vazio,
    }
