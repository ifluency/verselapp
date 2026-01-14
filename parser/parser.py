import re
import io
import pdfplumber
import pandas as pd


RE_ITEM = re.compile(r"^Item:\s*(\d+)\b")
RE_CATMAT = re.compile(r"(\d{6})\s*-\s*")
RE_DATE_TOKEN = re.compile(r"^\d{2}/\d{2}/\d{4}$")
RE_ROW_START = re.compile(r"^\s*(\d+)\s+([IVX]+)\s+", re.IGNORECASE)
RE_PAGE_MARK = re.compile(r"^\s*\d+\s+de\s+\d+\s*$", re.IGNORECASE)

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


def is_header_line(s: str) -> bool:
    s2 = clean_spaces(s).lower()
    return s2.startswith("nº inciso nome quantidade") or s2.startswith("nº inciso nome")


def normalize_text(s: str) -> str:
    """
    Normaliza padrões típicos do texto extraído:
    - Junta "gov." + "br" => "gov.br"
    - Separa "110Unidade" => "110 Unidade"
    - Normaliza "R$   150,4500" => "R$ 150,4500"
    """
    s = s.replace("\u00a0", " ")
    s = clean_spaces(s)

    # gov. br -> gov.br (inclui compras.gov. br)
    s = re.sub(r"(gov\.)\s*(br)\b", r"\1\2", s, flags=re.IGNORECASE)

    # separa número + letra colados: 110Unidade -> 110 Unidade
    s = re.sub(r"(\d)([A-Za-zÀ-ÿ])", r"\1 \2", s)
    s = re.sub(r"([A-Za-zÀ-ÿ])(\d)", r"\1 \2", s)

    # normaliza R$ + espaços
    s = re.sub(r"R\$\s+", "R$ ", s)

    return s


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
        # fallback: última data encontrada
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

    # quantidade: preferir padrão "<num> Unidade"
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

    # nome: tudo entre inciso e quantidade
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

    # buffer do registro atual (um registro pode ocupar várias linhas)
    current_row = None  # str
    current_row_item = None
    current_row_catmat = None

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

                # ignora marca de página "2 de 74"
                if RE_PAGE_MARK.fullmatch(line):
                    continue

                # detecta início de item
                m_item = RE_ITEM.match(line)
                if m_item:
                    # ao trocar de item, flush qualquer registro pendente
                    flush_current_row()
                    current_item = int(m_item.group(1))
                    current_catmat = None
                    in_table = False
                    continue

                # detecta CATMAT
                m_cat = RE_CATMAT.search(line)
                if m_cat:
                    current_catmat = m_cat.group(1)

                # detecta header de tabela (início da tabela de preços)
                if is_header_line(line):
                    flush_current_row()
                    in_table = True
                    continue

                if not in_table:
                    continue

                # se aparece novo registro (Nº Inciso ...)
                if RE_ROW_START.match(line):
                    # flush do anterior antes de iniciar outro
                    flush_current_row()
                    current_row = line
                    current_row_item = current_item
                    current_row_catmat = current_catmat
                else:
                    # continuação do registro atual (nome quebrado, "gov.br" quebrado, etc.)
                    if current_row:
                        current_row = clean_spaces(current_row + " " + line)
                    else:
                        # linha solta dentro da tabela sem registro iniciado: ignora
                        continue

    # flush final
    flush_current_row()

    df = pd.DataFrame(records, columns=FINAL_COLUMNS)

    # somente Compõe = Sim
    if "Compõe" in df.columns:
        df = df[df["Compõe"] == "Sim"].copy()

    df.reset_index(drop=True, inplace=True)

    # garante colunas e ordem
    for col in FINAL_COLUMNS:
        if col not in df.columns:
            df[col] = None
    df = df[FINAL_COLUMNS]

    return df


def validate_extraction(df: pd.DataFrame) -> dict:
    """
    Validação automática para detectar PDFs “fora do padrão”:
    - % de Nome vazio
    - % de Nome sem 'gov.br' (no Compras.gov.br quase sempre tem)
    """
    total = int(len(df))
    if total == 0:
        return {
            "total_rows": 0,
            "rows_nome_vazio": 0,
            "pct_nome_vazio": 0.0,
            "rows_nome_sem_govbr": 0,
            "pct_nome_sem_govbr": 0.0,
        }

    nome_series = df["Nome"].fillna("").astype(str).str.strip()
    rows_nome_vazio = int((nome_series == "").sum())
    pct_nome_vazio = round((rows_nome_vazio / total) * 100, 2)

    rows_nome_sem_govbr = int((~nome_series.str.lower().str.contains("gov.br")).sum())
    pct_nome_sem_govbr = round((rows_nome_sem_govbr / total) * 100, 2)

    return {
        "total_rows": total,
        "rows_nome_vazio": rows_nome_vazio,
        "pct_nome_vazio": pct_nome_vazio,
        "rows_nome_sem_govbr": rows_nome_sem_govbr,
        "pct_nome_sem_govbr": pct_nome_sem_govbr,
    }
