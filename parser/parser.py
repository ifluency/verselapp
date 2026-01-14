import re
import io
import pdfplumber
import pandas as pd


RE_ITEM = re.compile(r"^Item:\s*(\d+)\b")
RE_CATMAT = re.compile(r"(\d{6})\s*-\s*")
RE_DATE = re.compile(r"^\d{2}/\d{2}/\d{4}$")

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
    """Normaliza espaços para evitar quebra de layout."""
    return re.sub(r"\s+", " ", (s or "")).strip()


def is_noise_line(line: str) -> bool:
    s = clean_spaces(line)
    if not s:
        return True

    if s.startswith(("Relatório", "Relatorio", "Informações", "Informacoes", "Totaldeitens", "Itens cotados")):
        return True
    if s.startswith(("NúmerodaPesquisa", "Número da Pesquisa", "Título:", "Observações:", "1de", "2de", "3de")):
        return True
    if s.startswith(("Legenda:", "Compraouitem", "Compra ou item")):
        return True
    if s.startswith(("Descriçãodoitem", "Descrição do item", "Descriçao do item")):
        return True
    if s.startswith("Nº") and "Inciso" in s:
        return True

    return False


def looks_like_row(line: str) -> bool:
    """
    Detecta se uma linha parece registro da tabela:
    começa com Nº e Inciso romano, termina com Sim/Não, tem data e tem R$.
    """
    s = clean_spaces(line)
    toks = s.split(" ")
    if len(toks) < 6:
        return False
    if not toks[0].isdigit():
        return False
    if not re.fullmatch(r"[IVX]+", toks[1]):
        return False
    if toks[-1] not in ("Sim", "Não"):
        return False
    if not RE_DATE.fullmatch(toks[-2]):
        return False
    if not any(t.startswith("R$") for t in toks):
        return False
    return True


def parse_row(line: str):
    """
    Parsing por tokens, aceitando variação com/sem unidade:
    Nº Inciso Nome Quantidade [Unidade] R$... Data Sim/Não

    Observação: a coluna Unidade não é exportada; ela só pode existir no texto.
    """
    s = clean_spaces(line)
    toks = s.split(" ")

    no = toks[0]
    inciso = toks[1]
    compoe = toks[-1]
    data = toks[-2]

    price_idx = None
    for i, t in enumerate(toks):
        if t.startswith("R$"):
            price_idx = i
            break
    if price_idx is None:
        return None

    preco = toks[price_idx]

    # Quantidade: antes do R$ (sem unidade) ou 2 antes (com unidade)
    if price_idx - 1 >= 0 and re.fullmatch(r"\d+(?:[.,]\d+)?", toks[price_idx - 1]):
        qtd = toks[price_idx - 1]
        name_end = price_idx - 1
    elif price_idx - 2 >= 0 and re.fullmatch(r"\d+(?:[.,]\d+)?", toks[price_idx - 2]):
        qtd = toks[price_idx - 2]
        name_end = price_idx - 2
    else:
        return None

    nome_parte = " ".join(toks[2:name_end]).strip()

    return {
        "Nº": no,
        "Inciso": inciso,
        "Nome_parte": nome_parte,
        "Quantidade": qtd,
        "Preço unitário": preco,
        "Data": data,
        "Compõe": compoe,
    }


def normalize_price(p: str) -> str:
    if not p:
        return p
    return "R$" + re.sub(r"^R\$\s*", "", p.strip())


def process_pdf_bytes(pdf_bytes: bytes) -> pd.DataFrame:
    records = []

    current_item = None
    current_catmat = None
    last_company_line = None

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text(layout=True) or ""
            lines = page_text.splitlines()

            for i in range(len(lines)):
                line = clean_spaces(lines[i])
                if not line:
                    continue

                # Item
                m_item = RE_ITEM.match(line)
                if m_item:
                    current_item = int(m_item.group(1))
                    current_catmat = None
                    last_company_line = None
                    continue

                # CATMAT
                m_cat = RE_CATMAT.search(line)
                if m_cat:
                    current_catmat = m_cat.group(1)

                # Ruído (mas não exclui "Fornecedor")
                if is_noise_line(line) and line != "Fornecedor":
                    continue

                # Possível linha de razão social antes (para casos em que quebra)
                if (not looks_like_row(line)) and line != "Fornecedor":
                    # evita capturar domínios/linhas de portal
                    if ("gov.br" not in line.lower()) and ("compras.gov.br" not in line.lower()):
                        if 5 <= len(line) <= 160:
                            last_company_line = line

                # Registro de tabela
                if looks_like_row(line):
                    parsed = parse_row(line)
                    if not parsed:
                        continue

                    next_line = clean_spaces(lines[i + 1]) if i + 1 < len(lines) else ""
                    add_fornecedor = (next_line == "Fornecedor")

                    # Nome final: mantém como o PDF “entrega”, só juntando a linha anterior se houver quebra
                    nome_final = parsed["Nome_parte"] or ""
                    if last_company_line:
                        nome_final = (last_company_line + " " + nome_final).strip()
                    if add_fornecedor:
                        nome_final = (nome_final + " - Fornecedor").strip()

                    nome_final = clean_spaces(nome_final)

                    records.append({
                        "Item": f"Item {current_item}" if current_item is not None else None,
                        "CATMAT": current_catmat,
                        "Nº": parsed["Nº"],
                        "Inciso": parsed["Inciso"],
                        "Nome": nome_final,
                        "Quantidade": parsed["Quantidade"],
                        "Preço unitário": normalize_price(parsed["Preço unitário"]),
                        "Data": parsed["Data"],
                        "Compõe": parsed["Compõe"],
                    })

    df = pd.DataFrame(records, columns=FINAL_COLUMNS)

    # Mantém somente Compõe = Sim
    if "Compõe" in df.columns:
        df = df[df["Compõe"] == "Sim"].copy()

    df.reset_index(drop=True, inplace=True)

    # Garante colunas e ordem final
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
