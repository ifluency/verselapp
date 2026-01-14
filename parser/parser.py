import re
import io
import pdfplumber
import pandas as pd


RE_ITEM = re.compile(r"^Item:\s*(\d+)\b")
RE_CATMAT = re.compile(r"(\d{6})\s*-\s*")
RE_DATE = re.compile(r"\b\d{2}/\d{2}/\d{4}\b")
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


def is_header_line(s: str) -> bool:
    s2 = clean_spaces(s).lower()
    return s2.startswith("nº inciso nome quantidade") or s2.startswith("nº inciso nome")


def is_page_marker(s: str) -> bool:
    # Ex.: "2 de 74"
    s2 = clean_spaces(s).lower()
    return bool(re.fullmatch(r"\d+\s+de\s+\d+", s2))


def normalize_line_for_join(s: str) -> str:
    """
    Normaliza padrões do PDF antes de tokenizar:
    - Junta "gov." + "br" => "gov.br"
    - Separa "110Unidade" => "110 Unidade"
    - Normaliza "R$   150,4500" => "R$ 150,4500"
    """
    s = s.replace("\u00a0", " ")
    s = clean_spaces(s)

    s = re.sub(r"(gov\.)\s*(br)\b", r"\1\2", s, flags=re.IGNORECASE)

    # 110Unidade -> 110 Unidade
    s = re.sub(r"(\d)([A-Za-zÀ-ÿ])", r"\1 \2", s)
    s = re.sub(r"([A-Za-zÀ-ÿ])(\d)", r"\1 \2", s)

    s = re.sub(r"R\$\s+", "R$ ", s)

    return s


def record_is_complete(rec: str) -> bool:
    s = clean_spaces(rec)
    if not s:
        return False
    if not (s.endswith("Sim") or s.endswith("Não")):
        return False
    if not RE_DATE.search(s):
        return False
    if "R$" not in s:
        return False
    return True


def _find_price_token_index(toks):
    for i, t in enumerate(toks):
        if t == "R$" or t.startswith("R$"):
            return i
    return None


def _extract_price_raw(toks, r_idx):
    # Retorna somente o número, sem "R$"
    if r_idx is None:
        return None
    if toks[r_idx] == "R$":
        if r_idx + 1 >= len(toks):
            return None
        return toks[r_idx + 1]
    return toks[r_idx].replace("R$", "").strip()


def _find_quantity_index(toks, r_idx):
    """
    Preferência:
    - número seguido de Unidade (mais confiável nesses relatórios)
    Fallback:
    - último número antes de R$
    """
    for i in range(2, len(toks) - 1):
        if re.fullmatch(r"\d+(?:[.,]\d+)?", toks[i]) and toks[i + 1].lower().startswith("unidade"):
            return i

    if r_idx is not None:
        for j in range(r_idx - 1, 1, -1):
            if re.fullmatch(r"\d+(?:[.,]\d+)?", toks[j]):
                return j
    return None


def parse_record(rec: str):
    s = normalize_line_for_join(rec)
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

    # Data: penúltimo token normalmente, ou última data encontrada
    data = toks[-2]
    if not re.fullmatch(r"\d{2}/\d{2}/\d{4}", data):
        dates = [t for t in toks if re.fullmatch(r"\d{2}/\d{2}/\d{4}", t)]
        if not dates:
            return None
        data = dates[-1]

    r_idx = _find_price_token_index(toks)
    if r_idx is None:
        return None

    preco_raw = _extract_price_raw(toks, r_idx)
    if not preco_raw:
        return None

    qtd_idx = _find_quantity_index(toks, r_idx)
    if qtd_idx is None:
        return None
    quantidade = toks[qtd_idx]

    nome_tokens = toks[2:qtd_idx]
    nome = clean_spaces(" ".join(nome_tokens))

    return {
        "Nº": no,
        "Inciso": inciso,
        "Nome": nome,
        "Quantidade": quantidade,
        "Preço unitário": preco_raw,
        "Data": data,
        "Compõe": compoe,
    }


def looks_like_name_fragment(line: str) -> bool:
    """
    Heurística: linha que parece parte do nome do órgão/fornecedor.
    - contém 'compras' ou 'gov.br'
    - ou termina com '-' (muito comum)
    - ou é uma linha bem "textual" (não começa com número/romano)
    """
    s = normalize_line_for_join(line)
    s_low = s.lower()
    if "compras" in s_low or "gov.br" in s_low:
        return True
    if s.endswith("-"):
        return True
    # Evitar capturar linhas numéricas soltas
    if RE_ROW_START.match(s):
        return False
    # Se é uma linha com bastante letras e poucos números, pode ser fragmento de nome
    letters = sum(ch.isalpha() for ch in s)
    digits = sum(ch.isdigit() for ch in s)
    if letters >= 8 and digits <= 2:
        return True
    return False


def insert_pending_name_if_needed(row_line: str, pending_name: str) -> str:
    """
    Se a linha do registro está no formato "Nº Inciso <Quantidade> Unidade R$ ...",
    insere o pending_name logo após o Inciso:
      "Nº Inciso {pending_name} <Quantidade> Unidade R$ ..."
    """
    if not pending_name:
        return row_line

    s = normalize_line_for_join(row_line)
    toks = s.split(" ")

    if len(toks) < 3:
        return row_line

    # toks[0]=Nº, toks[1]=Inciso, toks[2]=pode ser Quantidade
    if toks[0].isdigit() and re.fullmatch(r"[IVX]+", toks[1], flags=re.IGNORECASE):
        if re.fullmatch(r"\d+(?:[.,]\d+)?", toks[2]):
            # insere pending entre inciso e o resto
            new_s = f"{toks[0]} {toks[1]} {pending_name} " + " ".join(toks[2:])
            return clean_spaces(new_s)

    return row_line


def process_pdf_bytes(pdf_bytes: bytes) -> pd.DataFrame:
    records = []

    current_item = None
    current_catmat = None

    in_table = False
    buffer = ""
    pending_name = ""  # <-- linhas “soltas” que parecem parte do nome

    def flush_buffer():
        nonlocal buffer, pending_name
        if buffer and record_is_complete(buffer):
            parsed = parse_record(buffer)
            if parsed:
                records.append({
                    "Item": f"Item {current_item}" if current_item is not None else None,
                    "CATMAT": current_catmat,
                    **parsed
                })
        buffer = ""
        pending_name = ""

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text(layout=True) or ""
            lines = page_text.splitlines()

            for raw in lines:
                line = raw.replace("\u00a0", " ").rstrip("\n")
                line_clean = clean_spaces(line)
                if not line_clean:
                    continue

                if is_page_marker(line_clean):
                    continue

                # Item
                m_item = RE_ITEM.match(line_clean)
                if m_item:
                    flush_buffer()
                    in_table = False
                    current_item = int(m_item.group(1))
                    current_catmat = None
                    continue

                # CATMAT
                m_cat = RE_CATMAT.search(line_clean)
                if m_cat:
                    current_catmat = m_cat.group(1)

                # Header
                if is_header_line(line_clean):
                    in_table = True
                    buffer = ""
                    pending_name = ""
                    continue

                if not in_table:
                    continue

                line_norm = normalize_line_for_join(line_clean)

                # Novo registro?
                m_row = RE_ROW_START.match(line_norm)
                if m_row:
                    # Antes de iniciar novo, tenta finalizar o anterior
                    if buffer:
                        if record_is_complete(buffer):
                            parsed = parse_record(buffer)
                            if parsed:
                                # Se o nome veio vazio aqui, tenta usar pending_name (raro, mas possível)
                                if not parsed["Nome"] and pending_name:
                                    buffer2 = insert_pending_name_if_needed(buffer, pending_name)
                                    parsed2 = parse_record(buffer2)
                                    if parsed2:
                                        parsed = parsed2
                                records.append({
                                    "Item": f"Item {current_item}" if current_item is not None else None,
                                    "CATMAT": current_catmat,
                                    **parsed
                                })
                        buffer = ""
                        pending_name = ""

                    # Se a linha começar já com "Nº Inciso Quantidade...", injeta pending_name
                    line_norm = insert_pending_name_if_needed(line_norm, pending_name)
                    pending_name = ""

                    buffer = line_norm

                    # Se completar na mesma linha, parseia já
                    if record_is_complete(buffer):
                        parsed = parse_record(buffer)
                        if parsed:
                            records.append({
                                "Item": f"Item {current_item}" if current_item is not None else None,
                                "CATMAT": current_catmat,
                                **parsed
                            })
                        buffer = ""
                        pending_name = ""
                    continue

                # Continuação ou fragmento do nome
                if buffer:
                    buffer = clean_spaces(buffer + " " + line_norm)

                    # Se completou, parseia
                    if record_is_complete(buffer):
                        parsed = parse_record(buffer)

                        # Se nome veio vazio, tenta injetar pending_name e/ou heurística local
                        if parsed and not parsed["Nome"] and pending_name:
                            buffer2 = insert_pending_name_if_needed(buffer, pending_name)
                            parsed2 = parse_record(buffer2)
                            if parsed2:
                                parsed = parsed2

                        if parsed:
                            records.append({
                                "Item": f"Item {current_item}" if current_item is not None else None,
                                "CATMAT": current_catmat,
                                **parsed
                            })
                        buffer = ""
                        pending_name = ""

                else:
                    # Sem buffer: pode ser fragmento de nome que “escapou” do registro
                    if looks_like_name_fragment(line_norm):
                        pending_name = clean_spaces((pending_name + " " + line_norm).strip())

    # flush final
    if buffer and record_is_complete(buffer):
        parsed = parse_record(buffer)
        if parsed:
            records.append({
                "Item": f"Item {current_item}" if current_item is not None else None,
                "CATMAT": current_catmat,
                **parsed
            })

    df = pd.DataFrame(records, columns=FINAL_COLUMNS)

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
    """
    Validação automática (opcional):
    Retorna métricas para você alertar o usuário quando o PDF “quebrar padrão”.
    """
    total = int(len(df))
    if total == 0:
        return {
            "total_rows": 0,
            "pct_nome_vazio": 0.0,
            "rows_nome_vazio": 0,
        }

    nome_vazio = int((df["Nome"].fillna("").str.strip() == "").sum())
    pct = round((nome_vazio / total) * 100, 2)

    return {
        "total_rows": total,
        "rows_nome_vazio": nome_vazio,
        "pct_nome_vazio": pct,
    }
