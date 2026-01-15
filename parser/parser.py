import re
import io
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


def clean_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def normalize_text(s: str) -> str:
    s = (s or "").replace("\u00a0", " ")
    s = clean_spaces(s)

    # gov. br -> gov.br
    s = re.sub(r"(gov\.)\s*(br)\b", r"\1\2", s, flags=re.IGNORECASE)

    # 110Unidade -> 110 Unidade
    s = re.sub(r"(\d)([A-Za-zÀ-ÿ])", r"\1 \2", s)
    s = re.sub(r"([A-Za-zÀ-ÿ])(\d)", r"\1 \2", s)

    # R$ com espaços
    s = re.sub(r"R\$\s+", "R$ ", s)

    return s


def fold(s: str) -> str:
    """lower + normalize spaces to compare markers reliably"""
    return normalize_text(s).lower()


def is_table_on(line: str) -> bool:
    s = normalize_text(line)
    return ("Período:" in s) or ("Periodo:" in s)


def is_table_off(line: str) -> bool:
    s = normalize_text(line).lower()
    return s.startswith("legenda")


def is_header(line: str) -> bool:
    s = normalize_text(line).lower()
    return s.startswith("nº inciso nome quantidade")


def looks_like_name_fragment(line: str) -> bool:
    s = normalize_text(line)
    if not s:
        return False
    # linha de registro não é fragmento
    if RE_ROW_START.match(s):
        return False

    low = fold(s)

    # linhas clássicas do dump
    if low in ("br", "gov.br"):
        return True

    # texto majoritariamente (poucos números)
    letters = sum(ch.isalpha() for ch in s)
    digits = sum(ch.isdigit() for ch in s)
    if letters >= 6 and digits <= 1:
        return True

    # contém compras/gov
    if "compras.gov" in low or "gov.br" in low:
        return True

    return False


def parse_row_fields(row_line: str):
    """
    Parseia a linha principal do registro:
    Ex.: '4 I 110 Unidade R$ 150,4500 05/12/2025 Sim'
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

    # preço cru
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

    # Nome inline (quase sempre vazio no seu PDF, mas mantemos)
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


# Terminadores por inciso (regras que você passou)
INCISO_END_MARKERS = {
    "I": ["compras.gov.br"],                # pode vir quebrado (Compras.gov. + br / gov.br)
    "III": ["mídias especializadas", "midias especializadas"],
    "IV": ["fornecedor"],
}


def name_has_terminator(inciso: str, name: str) -> bool:
    inc = (inciso or "").upper()
    low = fold(name)
    for m in INCISO_END_MARKERS.get(inc, []):
        if fold(m) in low:
            return True
    return False


def cut_name_at_terminator(inciso: str, name: str) -> str:
    """Corta tudo depois do terminador do inciso (inclusive contaminações)."""
    inc = (inciso or "").upper()
    original = normalize_text(name)
    low = fold(original)

    # pega o primeiro marcador que aparecer e corta no fim dele
    best_idx = None
    best_len = None
    for m in INCISO_END_MARKERS.get(inc, []):
        mm = fold(m)
        idx = low.find(mm)
        if idx != -1:
            if best_idx is None or idx < best_idx:
                best_idx = idx
                best_len = len(m)

    if best_idx is None:
        return original.strip()

    # corta no "fim" do marcador encontrado dentro do texto original
    return original[: best_idx + best_len].strip()


def stitch_compras_gov_variants(text: str) -> str:
    """
    Normaliza variações que aparecem quebradas:
    - 'Compras.gov.' + 'br' (linha separada)
    - 'Compras.gov. br' -> 'Compras.gov.br'
    - 'gov.br' perdido -> se já existe 'Compras.gov.' antes
    """
    s = normalize_text(text)

    # compras.gov. br -> compras.gov.br
    s = re.sub(r"(Compras\.gov\.)\s*(br)\b", r"\1\2", s, flags=re.IGNORECASE)

    # casos estranhos: "Compras.gov." sem br depois (mantém)
    return s


def process_pdf_bytes(pdf_bytes: bytes) -> pd.DataFrame:
    records = []

    current_item = None
    current_catmat = None
    capture = False

    pending_prefix = ""     # prefixo antes do próximo registro (linhas soltas)
    current_fields = None
    current_name = ""       # nome do registro atual

    def finalize_current():
        nonlocal current_fields, current_name, pending_prefix
        if not current_fields:
            return

        name = stitch_compras_gov_variants(current_name)

        # corte duro pelo terminador do inciso (remove contaminação)
        name = cut_name_at_terminator(current_fields["Inciso"], name)

        name = normalize_text(name)

        records.append({
            "Item": f"Item {current_item}" if current_item is not None else None,
            "CATMAT": current_catmat,
            "Nº": current_fields["Nº"],
            "Inciso": current_fields["Inciso"],
            "Nome": name,
            "Quantidade": current_fields["Quantidade"],
            "Preço unitário": current_fields["Preço unitário"],
            "Data": current_fields["Data"],
            "Compõe": current_fields["Compõe"],
        })

        current_fields = None
        current_name = ""
        pending_prefix = ""

    def add_to_pending(fragment: str):
        nonlocal pending_prefix
        pending_prefix = clean_spaces((pending_prefix + " " + fragment).strip())

    def add_to_current(fragment: str):
        nonlocal current_name
        current_name = clean_spaces((current_name + " " + fragment).strip())

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
                    finalize_current()
                    capture = False
                    current_item = int(m_item.group(1))
                    current_catmat = None
                    pending_prefix = ""
                    continue

                # CATMAT
                m_cat = RE_CATMAT.search(line)
                if m_cat:
                    current_catmat = m_cat.group(1)

                # liga/desliga tabela
                if is_table_on(line):
                    finalize_current()
                    pending_prefix = ""
                    capture = True
                    continue
                if is_table_off(line):
                    finalize_current()
                    pending_prefix = ""
                    capture = False
                    continue

                if not capture:
                    continue

                s = normalize_text(line)
                if is_header(s):
                    continue

                # linha principal do registro
                if RE_ROW_START.match(s):
                    # Se já existe um registro em aberto:
                    # - se ele AINDA não atingiu o terminador do inciso,
                    #   então essa "nova linha de registro" na verdade é ruído de linearização:
                    #   Finaliza? NÃO. Trata a linha como fragmento (pending) do que está vindo.
                    if current_fields and not name_has_terminator(current_fields["Inciso"], current_name):
                        # não começamos novo registro antes de fechar o nome do atual
                        # essa linha costuma ser parte do layout; guarda como pending
                        add_to_current(s)  # anexa (vai ser cortado pelo terminador depois)
                        continue

                    # caso normal: fecha anterior e inicia novo
                    finalize_current()

                    fields = parse_row_fields(s)
                    if not fields:
                        if looks_like_name_fragment(s):
                            add_to_pending(s)
                        continue

                    current_fields = fields

                    parts = []
                    if pending_prefix:
                        parts.append(pending_prefix)
                    if fields.get("InlineNome"):
                        parts.append(fields["InlineNome"])
                    current_name = clean_spaces(" ".join(parts))
                    pending_prefix = ""
                    continue

                # fragmento de nome
                if looks_like_name_fragment(s):
                    if not current_fields:
                        # ainda não começou registro -> prefixo do próximo
                        add_to_pending(s)
                        continue

                    # estamos dentro de um registro: regra principal
                    # Se o registro ainda não atingiu o terminador do inciso, anexa SEM PENSAR
                    if not name_has_terminator(current_fields["Inciso"], current_name):
                        add_to_current(s)
                        continue

                    # Se já atingiu o terminador, isso pertence ao próximo registro -> vira pending
                    # (ex.: "EMPRESA BRASILEIRA..." depois do Compras.gov.br)
                    add_to_pending(s)
                    continue

                # ruído
                continue

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
