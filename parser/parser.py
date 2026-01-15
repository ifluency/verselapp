import re
import io
import unicodedata
import pdfplumber
import pandas as pd


RE_ITEM = re.compile(r"^Item:\s*(\d+)\b", re.IGNORECASE)
RE_CATMAT = re.compile(r"(\d{6})\s*-\s*")
RE_PAGE_MARK = re.compile(r"^\s*\d+\s+de\s+\d+\s*$", re.IGNORECASE)
RE_DATE_TOKEN = re.compile(r"^\d{2}/\d{2}/\d{4}$")
RE_ROW_START = re.compile(r"^\s*(\d+)\s+([IVX]+)\b", re.IGNORECASE)

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


# Regras de término do nome por Inciso (case/acento-insensitive)
INCISO_END_MARKERS = {
    "I": ["compras.gov.br"],
    "II": ["contratacoes similares", "contratações similares"],
    "III": ["midias especializadas", "mídias especializadas"],
    "IV": ["fornecedor"],
    "V": ["nota fiscal eletronica", "nota fiscal eletrônica"],
}


def clean_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def _fold(s: str) -> str:
    """
    Lowercase + remove acentos (para comparação robusta com markers).
    """
    s = (s or "").lower()
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return s


def normalize_text(s: str) -> str:
    s = (s or "").replace("\u00a0", " ")
    s = clean_spaces(s)

    # "Compras.gov. br" -> "Compras.gov.br" (e também "gov. br" -> "gov.br")
    s = re.sub(r"(compras\.gov\.)\s*(br)\b", r"\1\2", s, flags=re.IGNORECASE)
    s = re.sub(r"(gov\.)\s*(br)\b", r"\1\2", s, flags=re.IGNORECASE)

    # 110Unidade -> 110 Unidade (e o inverso)
    s = re.sub(r"(\d)([A-Za-zÀ-ÿ])", r"\1 \2", s)
    s = re.sub(r"([A-Za-zÀ-ÿ])(\d)", r"\1 \2", s)

    # R$ com espaços
    s = re.sub(r"R\$\s+", "R$ ", s)

    return s


def is_table_on(line: str) -> bool:
    s = normalize_text(line)
    return ("Período:" in s) or ("Periodo:" in s)


def is_table_off(line: str) -> bool:
    s = normalize_text(line).lower()
    return s.startswith("legenda")


def is_header(line: str) -> bool:
    s = normalize_text(line).lower()
    return s.startswith("nº inciso nome quantidade")


def contains_gov(line: str) -> bool:
    s = normalize_text(line).lower()
    return ("compras.gov" in s) or ("gov.br" in s)


def looks_like_name_fragment(line: str) -> bool:
    s = normalize_text(line)
    if not s:
        return False
    if RE_ROW_START.match(s):
        return False

    low = s.lower()

    # linhas clássicas do dump
    if low in ("br", "gov.br"):
        return True
    if "compras.gov" in low or "gov.br" in low:
        return True

    # normalmente são linhas textuais (quase sem números)
    letters = sum(ch.isalpha() for ch in s)
    digits = sum(ch.isdigit() for ch in s)
    if letters >= 6 and digits <= 1:
        return True

    return False


def nome_esta_completo(inciso: str, nome_atual: str) -> bool:
    """
    Se o inciso tiver marcador conhecido, só considera completo quando esse marcador aparecer.
    Caso contrário, libera (True) e deixa o lookahead decidir.
    """
    inc = (inciso or "").upper()
    markers = INCISO_END_MARKERS.get(inc)
    if not markers:
        return True
    n = _fold(nome_atual)
    return any(_fold(m) in n for m in markers)


def parse_row_fields(row_line: str):
    """
    Parseia a linha principal do registro:
    Ex.: '4 I 110 Unidade R$ 150,4500 05/12/2025 Sim'
    Ex.: '26 I SAÚDE DE DOURADOS - Compras.gov. 250 Unidade R$ ... Não'
    """
    s = normalize_text(row_line)
    toks = s.split(" ")

    if len(toks) < 6:
        return None
    if not toks[0].isdigit():
        return None
    if not re.fullmatch(r"[IVX]+", toks[1], flags=re.IGNORECASE):
        return None

    no = toks[0]
    inciso = toks[1].upper()

    compoe = toks[-1]
    if compoe not in ("Sim", "Não"):
        return None

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

    # parte do nome que pode aparecer dentro da própria linha (entre inciso e quantidade)
    inline_name = clean_spaces(" ".join(toks[2:qtd_idx]))

    return {
        "Nº": no,
        "Inciso": inciso,
        "InlineNome": inline_name,
        "Quantidade": quantidade,
        "Preço unitário": preco_raw,
        "Data": data,
        "Compõe": compoe,
    }


def process_pdf_bytes(pdf_bytes: bytes) -> pd.DataFrame:
    records = []

    current_item = None
    current_catmat = None
    capture = False

    pending_name = ""     # nome antes do próximo registro
    current_fields = None
    current_name = ""     # nome do registro atual

    def finalize_current():
        nonlocal current_fields, current_name, pending_name
        if not current_fields:
            return

        nome_final = normalize_text(clean_spaces(current_name))

        records.append({
            "Item": f"Item {current_item}" if current_item is not None else None,
            "CATMAT": current_catmat,
            "Nº": current_fields["Nº"],
            "Inciso": current_fields["Inciso"],
            "Nome": nome_final,
            "Quantidade": current_fields["Quantidade"],
            "Preço unitário": current_fields["Preço unitário"],
            "Data": current_fields["Data"],
            "Compõe": current_fields["Compõe"],
        })

        current_fields = None
        current_name = ""

    def add_to_pending(fragment: str):
        nonlocal pending_name
        pending_name = clean_spaces((pending_name + " " + fragment).strip())

    def add_to_current(fragment: str):
        nonlocal current_name
        current_name = clean_spaces((current_name + " " + fragment).strip())

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text(layout=True) or ""
            lines = text.splitlines()

            i = 0
            while i < len(lines):
                raw = lines[i]
                line = clean_spaces(raw.replace("\u00a0", " "))
                if not line:
                    i += 1
                    continue
                if RE_PAGE_MARK.fullmatch(line):
                    i += 1
                    continue

                # novo item
                m_item = RE_ITEM.match(line)
                if m_item:
                    finalize_current()
                    pending_name = ""
                    capture = False
                    current_item = int(m_item.group(1))
                    current_catmat = None
                    i += 1
                    continue

                # CATMAT
                m_cat = RE_CATMAT.search(line)
                if m_cat:
                    current_catmat = m_cat.group(1)

                # liga/desliga tabela
                if is_table_on(line):
                    finalize_current()
                    pending_name = ""
                    capture = True
                    i += 1
                    continue
                if is_table_off(line):
                    finalize_current()
                    pending_name = ""
                    capture = False
                    i += 1
                    continue

                if not capture:
                    i += 1
                    continue

                s = normalize_text(line)

                if is_header(s):
                    i += 1
                    continue

                # linha principal do registro
                if RE_ROW_START.match(s):
                    finalize_current()

                    fields = parse_row_fields(s)
                    if not fields:
                        # se não parsear, mas parecer nome, guarda como pending
                        if looks_like_name_fragment(s):
                            add_to_pending(s)
                        i += 1
                        continue

                    current_fields = fields

                    # Nome inicial = pending + inline (se houver)
                    parts = []
                    if pending_name:
                        parts.append(pending_name)
                    if fields.get("InlineNome"):
                        parts.append(fields["InlineNome"])
                    current_name = clean_spaces(" ".join(parts))

                    pending_name = ""
                    i += 1
                    continue

                # fragmento de nome
                if looks_like_name_fragment(s):
                    if not current_fields:
                        add_to_pending(s)
                        i += 1
                        continue

                    inciso = (current_fields.get("Inciso") or "").upper()

                    # REGRA-CHAVE: enquanto não fechar o nome pelo marcador do Inciso, anexa SEMPRE
                    if not nome_esta_completo(inciso, current_name):
                        add_to_current(s)
                        i += 1
                        continue

                    # Se já está completo, decide com lookahead:
                    # se a próxima linha (não vazia) começa um novo registro, então isso é do próximo nome.
                    nxt = ""
                    j = i + 1
                    while j < len(lines):
                        nxt_candidate = clean_spaces(lines[j].replace("\u00a0", " "))
                        nxt_candidate = normalize_text(nxt_candidate)
                        if nxt_candidate:
                            nxt = nxt_candidate
                            break
                        j += 1

                    if nxt and RE_ROW_START.match(nxt):
                        finalize_current()
                        add_to_pending(s)
                        i += 1
                        continue

                    add_to_current(s)
                    i += 1
                    continue

                # qualquer outra linha dentro da tabela é ruído
                i += 1

    finalize_current()

    df = pd.DataFrame(records, columns=FINAL_COLUMNS)

    # somente Compõe=Sim
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
