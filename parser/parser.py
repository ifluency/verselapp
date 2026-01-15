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
    """
    Normaliza padrões típicos do texto extraído:
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


def is_table_on(line: str) -> bool:
    s = normalize_text(line)
    return ("Período:" in s) or ("Periodo:" in s)


def is_table_off(line: str) -> bool:
    s = normalize_text(line).lower()
    return s.startswith("legenda")


def looks_like_name_fragment(line: str) -> bool:
    """
    Fragmentos de nome aparecem sozinhos no dump (antes/depois do registro),
    ex: 'MINISTERIO ... - Compras.gov.' ou 'br' ou 'gov.br' ou 'HOSPITALARES - Compras.gov.br'
    """
    s = normalize_text(line)
    if not s:
        return False
    if RE_ROW_START.match(s):
        return False

    low = s.lower()

    # casos óbvios do dump
    if low in ("br", "gov.br"):
        return True
    if "compras.gov" in low or "gov.br" in low:
        return True
    if s.endswith("-"):
        return True

    # linha predominantemente textual
    letters = sum(ch.isalpha() for ch in s)
    digits = sum(ch.isdigit() for ch in s)
    if letters >= 6 and digits <= 1:
        return True

    return False


def parse_row_fields(row_line: str):
    """
    Parseia a linha principal do registro:
    Ex (dump): '4 I 110 Unidade R$ 150,4500 05/12/2025 Sim'
    Ex (dump item 26): '26 I SAÚDE DE DOURADOS - Compras.gov. 250 Unidade R$ 112,7000 17/09/2025 Não'

    Retorna:
      no, inciso, inline_name_part, quantidade, preco_raw, data, compoe
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

    # data (última data)
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

    # trecho do nome que às vezes aparece dentro da própria linha (ex item 26)
    inline_name_part = clean_spaces(" ".join(toks[2:qtd_idx]))

    return {
        "Nº": no,
        "Inciso": inciso,
        "InlineNome": inline_name_part,
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

    # nome “pendente” acumulado até aparecer a próxima linha de registro
    pending_name = ""

    # registro atual (linha de Nº Inciso...)
    current_fields = None
    current_name = ""

    def finalize_current():
        nonlocal current_fields, current_name
        if not current_fields:
            return

        nome_final = normalize_text(clean_spaces(current_name))

        # grava
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
                    # fecha qualquer registro pendente do item anterior
                    finalize_current()
                    pending_name = ""
                    capture = False

                    current_item = int(m_item.group(1))
                    current_catmat = None
                    continue

                # CATMAT
                m_cat = RE_CATMAT.search(line)
                if m_cat:
                    current_catmat = m_cat.group(1)

                # liga/desliga captura por período/legenda
                if is_table_on(line):
                    finalize_current()
                    pending_name = ""
                    capture = True
                    continue
                if is_table_off(line):
                    finalize_current()
                    pending_name = ""
                    capture = False
                    continue

                if not capture:
                    continue

                s = normalize_text(line)

                # ignora o header textual da tabela
                if s.lower().startswith("nº inciso nome quantidade"):
                    continue

                # É uma linha de registro (Nº Inciso ...)
                if RE_ROW_START.match(s):
                    # ao iniciar um novo registro, finalize o anterior
                    finalize_current()

                    fields = parse_row_fields(s)
                    if not fields:
                        # se não parseou, trata como texto solto
                        if looks_like_name_fragment(s):
                            pending_name = clean_spaces((pending_name + " " + s).strip())
                        continue

                    current_fields = fields

                    # nome = pending_name (antes) + inline_name_part (se existir)
                    parts = []
                    if pending_name:
                        parts.append(pending_name)
                    if fields.get("InlineNome"):
                        parts.append(fields["InlineNome"])
                    current_name = clean_spaces(" ".join(parts))

                    pending_name = ""
                    continue

                # Não é linha de registro:
                # - se parece fragmento de nome e NÃO temos registro atual -> acumula no pending_name (vai para o PRÓXIMO registro)
                # - se temos registro atual -> é continuação do nome do registro atual
                if looks_like_name_fragment(s):
                    if current_fields:
                        # continuação do nome do registro atual (ex: 'br', 'gov.br', 'HOSPITALARES - ...')
                        current_name = clean_spaces((current_name + " " + s).strip())
                    else:
                        pending_name = clean_spaces((pending_name + " " + s).strip())
                    continue

                # Qualquer outra linha dentro do capture, ignoramos (ruído)
                continue

    # flush final
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


def dataframe_to_excel_bytes(df: pd.DataFrame) -> bytes:
    out = io.BytesIO()
    df.to_excel(out, index=False)
    out.seek(0)
    return out.read()


def validate_extraction(df: pd.DataFrame) -> dict:
    total = int(len(df))
    if total == 0:
        return {"total_rows": 0, "rows_nome_vazio": 0, "pct_nome_vazio": 0.0}

    nome_series = df["Nome"].fillna("").astype(str).str.strip()
    rows_nome_vazio = int((nome_series == "").sum())
    pct_nome_vazio = round((rows_nome_vazio / total) * 100, 2)
    return {"total_rows": total, "rows_nome_vazio": rows_nome_vazio, "pct_nome_vazio": pct_nome_vazio}
