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
    - Cria espaço entre "R$" e valor quando vierem separados
    """
    s = s.replace("\u00a0", " ")
    s = clean_spaces(s)

    # Junta gov. br -> gov.br (e também compras.gov. br)
    s = re.sub(r"(gov\.)\s*(br)\b", r"\1\2", s, flags=re.IGNORECASE)

    # Separa número + letra colados (110Unidade -> 110 Unidade)
    s = re.sub(r"(\d)([A-Za-zÀ-ÿ])", r"\1 \2", s)

    # Também separa letra + número colados (menos comum, mas ajuda)
    s = re.sub(r"([A-Za-zÀ-ÿ])(\d)", r"\1 \2", s)

    # Normaliza "R$ 150,4500" (deixa com espaço; combinaremos no parser)
    s = re.sub(r"R\$\s+", "R$ ", s)

    return s


def record_is_complete(rec: str) -> bool:
    """
    Um registro completo normalmente:
    - termina com Sim/Não
    - tem uma data dd/mm/aaaa
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


def parse_record(rec: str):
    """
    Parse de um registro lógico:
    Nº Inciso Nome... Quantidade Unidade? R$ Valor Data Sim/Não

    - Nome pode conter espaços e quebras já unidas
    - Unidade pode existir ou não (não exportamos)
    - Preço pode vir como "R$ 150,4500" (tokens separados)
    """
    s = normalize_line_for_join(rec)
    toks = s.split(" ")

    # Nº e Inciso
    if len(toks) < 6:
        return None
    if not toks[0].isdigit():
        return None
    if not re.fullmatch(r"[IVX]+", toks[1], flags=re.IGNORECASE):
        return None

    no = toks[0]
    inciso = toks[1]

    # Compõe (último token)
    compoe = toks[-1]
    if compoe not in ("Sim", "Não"):
        return None

    # Data (penúltimo token deve ser data)
    data = toks[-2]
    if not re.fullmatch(r"\d{2}/\d{2}/\d{4}", data):
        # às vezes a data pode não estar exatamente no penúltimo se houver ruído, então buscamos a última data
        dates = [t for t in toks if re.fullmatch(r"\d{2}/\d{2}/\d{4}", t)]
        if not dates:
            return None
        data = dates[-1]

    # Encontrar posição do "R$"
    r_idx = None
    for i, t in enumerate(toks):
        if t == "R$" or t.startswith("R$"):
            r_idx = i
            break
    if r_idx is None:
        return None

    # Preço: pode ser "R$" + "150,4500" ou "R$150,4500"
    if toks[r_idx] == "R$":
        if r_idx + 1 >= len(toks):
            return None
        preco_val = toks[r_idx + 1]
        preco = f"R${preco_val}"
        preco_end_idx = r_idx + 2
    else:
        # já veio junto
        preco = toks[r_idx]
        preco_end_idx = r_idx + 1

    # Quantidade: procurar o número imediatamente antes do "R$"
    # (como já normalizamos 110Unidade -> 110 Unidade, isso fica bem confiável)
    qtd_idx = None
    for j in range(r_idx - 1, 1, -1):
        if re.fullmatch(r"\d+(?:[.,]\d+)?", toks[j]):
            qtd_idx = j
            break
    if qtd_idx is None:
        return None

    quantidade = toks[qtd_idx]

    # Nome: tokens entre Inciso (idx 1) e Quantidade (qtd_idx)
    nome_tokens = toks[2:qtd_idx]
    nome = " ".join(nome_tokens).strip()

    # Remover "Unidade" do nome se ela tiver “escapado” (caso raro)
    nome = re.sub(r"\bUnidade\b", "", nome, flags=re.IGNORECASE).strip()
    nome = clean_spaces(nome)

    return {
        "Nº": no,
        "Inciso": inciso,
        "Nome": nome,
        "Quantidade": quantidade,
        "Preço unitário": preco,
        "Data": data,
        "Compõe": compoe,
    }


def process_pdf_bytes(pdf_bytes: bytes) -> pd.DataFrame:
    records = []

    current_item = None
    current_catmat = None

    # Controle do parsing por tabela
    in_table = False
    buffer = ""  # buffer do registro lógico atual

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text(layout=True) or ""
            lines = page_text.splitlines()

            for raw in lines:
                line = raw.replace("\u00a0", " ")
                line = line.rstrip("\n")
                line_clean = clean_spaces(line)

                if not line_clean:
                    continue

                # Detecta Item
                m_item = RE_ITEM.match(line_clean)
                if m_item:
                    # Antes de trocar de item, tenta flush do buffer
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

                # Header de tabela
                if is_header_line(line_clean):
                    in_table = True
                    buffer = ""
                    continue

                if not in_table:
                    continue

                # Normaliza linha para juntar quebras tipo "Compras.gov." + "br"
                line_norm = normalize_line_for_join(line_clean)

                # Começo de um registro novo?
                if RE_ROW_START.match(line_norm):
                    # flush do anterior
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
                    # Continuação (nome quebrado, gov.br quebrado etc.)
                    if buffer:
                        buffer = clean_spaces(buffer + " " + line_norm)
                    else:
                        # Linha perdida fora de registro — ignora
                        continue

                # Se já ficou completo, parseia e zera buffer
                if buffer and record_is_complete(buffer):
                    parsed = parse_record(buffer)
                    if parsed:
                        records.append({
                            "Item": f"Item {current_item}" if current_item is not None else None,
                            "CATMAT": current_catmat,
                            **parsed
                        })
                    buffer = ""

    # Monta DataFrame
    df = pd.DataFrame(records, columns=FINAL_COLUMNS)

    # Filtra apenas Compõe=Sim
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
