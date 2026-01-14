import re
import io
import pdfplumber
import pandas as pd


RE_ITEM = re.compile(r"^Item:\s*(\d+)\b")
RE_CATMAT = re.compile(r"(\d{6})\s*-\s*")
RE_DATE = re.compile(r"\b\d{2}/\d{2}/\d{4}\b")
RE_ROW_START = re.compile(r"^\s*(\d+)\s+([IVX]+)\s+", re.IGNORECASE)

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


def normalize_line_for_join(s: str) -> str:
    """
    Normaliza padrões típicos do PDF antes de tokenizar:
    - Junta "gov." + "br" => "gov.br"
    - Cria espaço entre número e letra em "110Unidade" => "110 Unidade"
    - Normaliza "R$   150,4500" => "R$ 150,4500"
    """
    s = s.replace("\u00a0", " ")
    s = clean_spaces(s)

    # Junta gov. br -> gov.br
    s = re.sub(r"(gov\.)\s*(br)\b", r"\1\2", s, flags=re.IGNORECASE)

    # Separa número + letra colados (110Unidade -> 110 Unidade)
    s = re.sub(r"(\d)([A-Za-zÀ-ÿ])", r"\1 \2", s)
    s = re.sub(r"([A-Za-zÀ-ÿ])(\d)", r"\1 \2", s)

    # Normaliza "R$  valor"
    s = re.sub(r"R\$\s+", "R$ ", s)

    return s


def record_is_complete(rec: str) -> bool:
    """
    Registro completo:
    - termina com Sim/Não
    - tem data dd/mm/aaaa
    - tem R$
    """
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
    """
    Retorna preço sem 'R$' (somente número com vírgula/ponto), ex:
    - ['R$', '150,4500'] => '150,4500'
    - ['R$150,4500'] => '150,4500'
    """
    if r_idx is None:
        return None

    if toks[r_idx] == "R$":
        if r_idx + 1 >= len(toks):
            return None
        return toks[r_idx + 1]
    else:
        # remove o prefixo R$
        return toks[r_idx].replace("R$", "").strip()


def _find_quantity_index(toks, r_idx):
    """
    Regra mais robusta para esse PDF:
    - Preferir padrão: <quantidade_numérica> <unidade>
      Ex: '110 Unidade'
    - Fallback: número imediatamente antes de R$ (considerando que pode existir unidade no meio)
    """
    # 1) Preferir "numero" seguido de "Unidade" (ou variações)
    for i in range(2, len(toks) - 1):
        if re.fullmatch(r"\d+(?:[.,]\d+)?", toks[i]) and toks[i + 1].lower().startswith("unidade"):
            return i

    # 2) Fallback: buscar o último número antes do token R$
    if r_idx is not None:
        for j in range(r_idx - 1, 1, -1):
            if re.fullmatch(r"\d+(?:[.,]\d+)?", toks[j]):
                return j

    return None


def parse_record(rec: str):
    """
    Parse de um registro lógico:
    Nº Inciso Nome... Quantidade Unidade? R$ Valor Data Sim/Não

    - Nome pode quebrar em linhas (já unidas no buffer)
    - Unidade pode existir; não exportamos
    - Preço pode vir como "R$ 150,4500" ou "R$150,4500"
    """
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

    # Data: normalmente penúltimo token; se não, pega a última data encontrada
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

    # Nome: entre Inciso (idx 1) e Quantidade (qtd_idx)
    nome_tokens = toks[2:qtd_idx]
    nome = clean_spaces(" ".join(nome_tokens))

    return {
        "Nº": no,
        "Inciso": inciso,
        "Nome": nome,
        "Quantidade": quantidade,
        "Preço unitário": preco_raw,  # <- SEM "R$"
        "Data": data,
        "Compõe": compoe,
    }


def process_pdf_bytes(pdf_bytes: bytes) -> pd.DataFrame:
    records = []

    current_item = None
    current_catmat = None

    in_table = False
    buffer = ""

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text(layout=True) or ""
            lines = page_text.splitlines()

            for raw in lines:
                line = raw.replace("\u00a0", " ").rstrip("\n")
                line_clean = clean_spaces(line)

                if not line_clean:
                    continue

                # Item
                m_item = RE_ITEM.match(line_clean)
                if m_item:
                    # flush do buffer antes de trocar item
                    if buffer and record_is_complete(buffer):
                        parsed = parse_record(buffer)
                        if parsed:
                            records.append({
                                "Item": f"Item {current_item}" if current_item is not None else None,
                                "CATMAT": current_catmat,
                                **parsed
                            })
                    buffer = ""
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
                    continue

                if not in_table:
                    continue

                line_norm = normalize_line_for_join(line_clean)

                # Novo registro
                if RE_ROW_START.match(line_norm):
                    # flush anterior
                    if buffer:
                        if record_is_complete(buffer):
                            parsed = parse_record(buffer)
                            if parsed:
                                records.append({
                                    "Item": f"Item {current_item}" if current_item is not None else None,
                                    "CATMAT": current_catmat,
                                    **parsed
                                })
                        buffer = ""

                    buffer = line_norm
                else:
                    # Continuação (nome quebrado / gov.br quebrado etc.)
                    if buffer:
                        buffer = clean_spaces(buffer + " " + line_norm)
                    else:
                        continue

                # Parse imediato se completo
                if buffer and record_is_complete(buffer):
                    parsed = parse_record(buffer)
                    if parsed:
                        records.append({
                            "Item": f"Item {current_item}" if current_item is not None else None,
                            "CATMAT": current_catmat,
                            **parsed
                        })
                    buffer = ""

    df = pd.DataFrame(records, columns=FINAL_COLUMNS)

    # Filtra apenas Compõe = Sim
    if "Compõe" in df.columns:
        df = df[df["Compõe"] == "Sim"].copy()

    df.reset_index(drop=True, inplace=True)

    # Garante colunas e ordem
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
