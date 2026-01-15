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


def is_table_on(line: str) -> bool:
    s = normalize_text(line)
    return ("Período:" in s) or ("Periodo:" in s)


def is_table_off(line: str) -> bool:
    s = normalize_text(line).lower()
    return s.startswith("legenda")


def is_header(line: str) -> bool:
    s = normalize_text(line).lower()
    return s.startswith("nº inciso nome quantidade")


def is_row_line(line: str) -> bool:
    return RE_ROW_START.match(normalize_text(line)) is not None


def looks_like_name_fragment(line: str) -> bool:
    s = normalize_text(line)
    if not s or is_row_line(s) or is_header(s):
        return False

    low = s.lower()

    # tokens comuns no dump
    if low in ("br", "gov.br"):
        return True

    # linhas com gov / compras
    if "compras.gov" in low or "gov.br" in low:
        return True

    # linha majoritariamente textual (pouco número)
    letters = sum(ch.isalpha() for ch in s)
    digits = sum(ch.isdigit() for ch in s)
    if letters >= 6 and digits <= 1:
        return True

    return False


def next_nonempty(lines, start_idx):
    """Retorna (idx, line) do próximo não-vazio a partir de start_idx; senão (None, None)."""
    n = len(lines)
    i = start_idx
    while i < n:
        s = normalize_text(lines[i])
        if s:
            return i, s
        i += 1
    return None, None


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
    inciso = toks[1]

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

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text(layout=True) or ""
            raw_lines = text.splitlines()

            # limpa ruído (ex: "1 de 74")
            lines = []
            for ln in raw_lines:
                ln2 = clean_spaces(ln.replace("\u00a0", " "))
                if not ln2:
                    continue
                if RE_PAGE_MARK.fullmatch(ln2):
                    continue
                lines.append(ln2)

            i = 0
            while i < len(lines):
                line = lines[i]

                # novo item
                m_item = RE_ITEM.match(line)
                if m_item:
                    current_item = int(m_item.group(1))
                    current_catmat = None
                    capture = False
                    i += 1
                    continue

                # CATMAT
                m_cat = RE_CATMAT.search(line)
                if m_cat:
                    current_catmat = m_cat.group(1)

                # liga/desliga tabela
                if is_table_on(line):
                    capture = True
                    i += 1
                    continue
                if is_table_off(line):
                    capture = False
                    i += 1
                    continue

                if not capture:
                    i += 1
                    continue

                # ignora header
                if is_header(line):
                    i += 1
                    continue

                # se achou linha de registro
                if is_row_line(line):
                    fields = parse_row_fields(line)
                    if not fields:
                        i += 1
                        continue

                    # ====== monta NOME com janela antes/depois + lookahead ======

                    # 1) prefixo: pega 1-3 linhas anteriores (somente se forem fragmentos de nome)
                    prefix_parts = []
                    back = i - 1
                    # no dump, geralmente é 1 linha anterior, às vezes 2 ("MMS-FUNDAÇÃO..." + inline)
                    while back >= 0 and len(prefix_parts) < 3:
                        prev = lines[back]
                        if is_row_line(prev) or is_header(prev) or is_table_on(prev) or is_table_off(prev) or RE_ITEM.match(prev):
                            break
                        if looks_like_name_fragment(prev):
                            # insere no início (ordem correta)
                            prefix_parts.insert(0, normalize_text(prev))
                            back -= 1
                            continue
                        break

                    # 2) inline nome (quando aparece dentro da linha do registro, ex item 26)
                    inline_part = fields.get("InlineNome") or ""
                    inline_part = normalize_text(inline_part)

                    # 3) sufixo: pega linhas seguintes SOMENTE se não forem prefixo do próximo registro
                    suffix_parts = []
                    fwd = i + 1
                    while fwd < len(lines) and len(suffix_parts) < 3:
                        nxt = lines[fwd]

                        # para se começar outro registro / header / item / período / legenda
                        if is_row_line(nxt) or is_header(nxt) or is_table_on(nxt) or is_table_off(nxt) or RE_ITEM.match(nxt):
                            break

                        if not looks_like_name_fragment(nxt):
                            break

                        nxt_norm = normalize_text(nxt).strip().lower()

                        # "br" e "gov.br" sempre pertencem ao registro atual (sufixo clássico)
                        if nxt_norm in ("br", "gov.br"):
                            suffix_parts.append(normalize_text(nxt))
                            fwd += 1
                            continue

                        # lookahead: se a PRÓXIMA linha não vazia após esse fragmento é uma row_line,
                        # então ESSE fragmento é prefixo do próximo registro -> NÃO anexa, e para.
                        j, look = next_nonempty(lines, fwd + 1)
                        if look is not None and is_row_line(look):
                            break

                        # caso contrário, é continuação do nome atual
                        suffix_parts.append(normalize_text(nxt))
                        fwd += 1

                    # monta nome final
                    nome_parts = []
                    nome_parts.extend(prefix_parts)

                    # Só inclui inline se tiver texto real
                    if inline_part:
                        nome_parts.append(inline_part)

                    nome_parts.extend(suffix_parts)

                    nome = normalize_text(clean_spaces(" ".join(nome_parts)))

                    records.append({
                        "Item": f"Item {current_item}" if current_item is not None else None,
                        "CATMAT": current_catmat,
                        "Nº": fields["Nº"],
                        "Inciso": fields["Inciso"],
                        "Nome": nome,
                        "Quantidade": fields["Quantidade"],
                        "Preço unitário": fields["Preço unitário"],
                        "Data": fields["Data"],
                        "Compõe": fields["Compõe"],
                    })

                    i += 1
                    continue

                i += 1

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
