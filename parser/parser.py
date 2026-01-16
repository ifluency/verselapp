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

# Regras que você passou
INCISO_TERMINATORS = {
    "I": "Compras.gov.br",
    "III": "Mídias Especializadas",
    "IV": "Fornecedor",
    # II / V: sem terminador fixo (por enquanto)
}


def clean_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def normalize_text(s: str) -> str:
    s = (s or "").replace("\u00a0", " ")
    s = clean_spaces(s)

    # gov. br -> gov.br
    s = re.sub(r"(gov\.)\s*(br)\b", r"\1\2", s, flags=re.IGNORECASE)

    # Compras.gov. br -> Compras.gov.br
    s = re.sub(r"(Compras\.gov\.)\s*(br)\b", r"\1\2", s, flags=re.IGNORECASE)

    # “110Unidade” -> “110 Unidade”
    s = re.sub(r"(\d)([A-Za-zÀ-ÿ])", r"\1 \2", s)
    s = re.sub(r"([A-Za-zÀ-ÿ])(\d)", r"\1 \2", s)

    # R$ com espaços
    s = re.sub(r"R\$\s+", "R$ ", s)

    return s


def is_table_on(line: str) -> bool:
    s = normalize_text(line)
    # no seu PDF: antes da tabela sempre aparece "Período: 12 Meses"
    return ("Período:" in s) or ("Periodo:" in s)


def is_table_off(line: str) -> bool:
    s = normalize_text(line).lower()
    return s.startswith("legenda")


def is_header(line: str) -> bool:
    s = normalize_text(line).lower()
    return s.startswith("nº inciso nome quantidade")


def contains_terminator(text: str, inciso: str) -> bool:
    term = INCISO_TERMINATORS.get((inciso or "").upper())
    if not term:
        return False
    return re.search(re.escape(term), text or "", flags=re.IGNORECASE) is not None


def cut_name_by_inciso(nome_raw: str, inciso: str) -> str:
    """
    Corta o nome exatamente no terminador do inciso, quando existir.
    Ex:
      I  -> ... Compras.gov.br
      III-> ... Mídias Especializadas
      IV -> ... Fornecedor
    """
    nome_raw = normalize_text(nome_raw)
    inciso = (inciso or "").upper()

    term = INCISO_TERMINATORS.get(inciso)
    if not term:
        return nome_raw.strip()

    m = re.search(re.escape(term), nome_raw, flags=re.IGNORECASE)
    if not m:
        return nome_raw.strip()

    return nome_raw[: m.end()].strip()


def strip_table_bits_from_name_fragment(fragment: str) -> str:
    """
    Remove padrões típicos “Quantidade + Unidade” que às vezes grudam no nome
    quando o pdfplumber quebra esquisito.

    Ex:
      "ESP-HOSPITAL ... 2925 Embalagem RIBEIRAO ... - Compras.gov.br"
      -> remove "2925 Embalagem"
    """
    s = normalize_text(fragment)

    # remove “<num> Unidade/Embalagem”
    s = re.sub(r"\b\d+\s+(Unidade|Embalagem)\b", "", s, flags=re.IGNORECASE)

    # remove casos onde ficou “<num>  Embalagem” duplicado por espaços
    s = re.sub(r"\b\d+\s+(Unidade|Embalagem)\b", "", s, flags=re.IGNORECASE)

    return clean_spaces(s)


def looks_like_name_line(line: str) -> bool:
    """
    Linhas de nome do PDF:
    - não começam com “Nº Inciso ...”
    - não começam com o registro (número + inciso)
    - geralmente têm letras e não têm “R$” nem data
    """
    s = normalize_text(line)
    if not s:
        return False
    if is_header(s):
        return False
    if RE_ROW_START.match(s):
        return False
    if "R$" in s:
        return False
    if any(RE_DATE_TOKEN.fullmatch(tok) for tok in s.split()):
        return False

    # precisa ter letras
    letters = sum(ch.isalpha() for ch in s)
    return letters >= 3


def is_br_fragment(line: str) -> bool:
    s = normalize_text(line).lower()
    return s in ("br", "gov.br")


def parse_row_fields(row_line: str):
    """
    Parseia a linha do registro:
      "4 I 110 Unidade R$ 150,4500 05/12/2025 Sim"
    Nome normalmente NÃO vem aqui (vem em linhas anteriores)
    """
    s = normalize_text(row_line)
    toks = s.split()

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

    # quantidade: procurar "<num> Unidade/Embalagem"
    qtd_idx = None
    for i in range(2, len(toks) - 1):
        if re.fullmatch(r"\d+(?:[.,]\d+)?", toks[i]) and (toks[i + 1].lower().startswith("unidade") or toks[i + 1].lower().startswith("embalagem")):
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

    return {
        "Nº": no,
        "Inciso": inciso,
        "Quantidade": quantidade,
        "Preço unitário": preco_raw,
        "Data": data,
        "Compõe": compoe,
    }


def process_pdf_bytes_debug(pdf_bytes: bytes) -> tuple[pd.DataFrame, list]:
    """
    Retorna:
      df_final (FINAL_COLUMNS)
      debug_records (para debug_dump)
    """
    records = []
    debug_records = []

    current_item = None
    current_catmat = None

    capture = False

    # Buffer de nome que precede o registro
    name_buffer = []

    # Registro atual
    current_fields = None
    current_name = ""
    name_fragments = []

    def start_new_record(fields: dict):
        nonlocal current_fields, current_name, name_fragments, name_buffer
        current_fields = fields

        # Nome vem do buffer antes da linha do registro
        raw_name = clean_spaces(" ".join(name_buffer))
        raw_name = strip_table_bits_from_name_fragment(raw_name)

        current_name = raw_name
        name_fragments = name_buffer.copy()
        name_buffer = []

    def finalize_current():
        nonlocal current_fields, current_name, name_fragments
        if not current_fields:
            return

        inciso = current_fields["Inciso"]
        nome_raw = normalize_text(current_name)
        nome_final = cut_name_by_inciso(nome_raw, inciso)

        row = {
            "Item": f"Item {current_item}" if current_item is not None else None,
            "CATMAT": current_catmat,
            "Nº": current_fields["Nº"],
            "Inciso": inciso,
            "Nome": nome_final,
            "Quantidade": current_fields["Quantidade"],
            "Preço unitário": current_fields["Preço unitário"],
            "Data": current_fields["Data"],
            "Compõe": current_fields["Compõe"],
        }
        records.append(row)

        debug_records.append({
            "Item": row["Item"],
            "CATMAT": row["CATMAT"],
            "Nº": row["Nº"],
            "Inciso": row["Inciso"],
            "Nome_fragments": [normalize_text(x) for x in name_fragments],
            "Nome_raw": nome_raw,
            "Nome_final": nome_final,
        })

        current_fields = None
        current_name = ""
        name_fragments = []

    def add_to_buffer(line: str):
        s = strip_table_bits_from_name_fragment(line)
        if s:
            name_buffer.append(s)

    def add_to_current_name(line: str):
        nonlocal current_name, name_fragments
        s = strip_table_bits_from_name_fragment(line)
        if not s:
            return
        current_name = clean_spaces((current_name + " " + s).strip())
        name_fragments.append(s)

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
                    name_buffer = []
                    capture = False
                    current_item = int(m_item.group(1))
                    current_catmat = None
                    continue

                # CATMAT
                m_cat = RE_CATMAT.search(line)
                if m_cat:
                    current_catmat = m_cat.group(1)

                # liga/desliga tabela
                if is_table_on(line):
                    finalize_current()
                    name_buffer = []
                    capture = True
                    continue

                if is_table_off(line):
                    finalize_current()
                    name_buffer = []
                    capture = False
                    continue

                if not capture:
                    continue

                s = normalize_text(line)
                if is_header(s):
                    continue

                # linha do registro: "Nº Inciso ..."
                if RE_ROW_START.match(s):
                    fields = parse_row_fields(s)
                    if not fields:
                        # se falhar, pode ser ruído -> tenta buffer se parecer nome
                        if looks_like_name_line(s):
                            add_to_buffer(s)
                        continue

                    # antes de iniciar novo, finaliza anterior
                    finalize_current()
                    start_new_record(fields)
                    continue

                # demais linhas dentro da tabela:
                # Regra:
                # - Se já existe registro atual, só anexamos ao nome atual se:
                #     a) for 'br' / 'gov.br' (caso Compras.gov.)
                #     b) para Inciso IV, completar até conter "Fornecedor"
                #     c) para Inciso I, completar se ainda não tem "Compras.gov.br" e o fragmento ajuda a completar
                if current_fields:
                    inciso = current_fields["Inciso"]

                    # Continuação "br" / "gov.br"
                    if is_br_fragment(s):
                        add_to_current_name(s)
                        continue

                    # Inciso I: aceitar continuação se ainda não tem terminador
                    if inciso == "I" and (not contains_terminator(current_name, "I")):
                        # se é uma linha que parece parte do nome (ex.: "HOSPITALARES - Compras.gov.br")
                        if looks_like_name_line(s):
                            add_to_current_name(s)
                            continue

                    # Inciso IV: aceitar continuação até aparecer "Fornecedor"
                    if inciso == "IV" and (not contains_terminator(current_name, "IV")):
                        if looks_like_name_line(s):
                            add_to_current_name(s)
                            continue

                    # Inciso III: aceitar continuação até "Mídias Especializadas"
                    if inciso == "III" and (not contains_terminator(current_name, "III")):
                        if looks_like_name_line(s):
                            add_to_current_name(s)
                            continue

                    # Se chegou aqui: não é continuação do nome atual
                    # então, se parecer nome, vai pro buffer do próximo registro
                    if looks_like_name_line(s):
                        add_to_buffer(s)
                    continue

                # sem registro atual ainda -> buffer de nome
                if looks_like_name_line(s):
                    add_to_buffer(s)
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

    return df, debug_records


def process_pdf_bytes(pdf_bytes: bytes) -> pd.DataFrame:
    df, _ = process_pdf_bytes_debug(pdf_bytes)
    return df


def validate_extraction(df: pd.DataFrame) -> dict:
    total = int(len(df))
    if total == 0:
        return {"total_rows": 0, "rows_nome_vazio": 0, "pct_nome_vazio": 0.0}

    nome_series = df["Nome"].fillna("").astype(str).str.strip()
    rows_nome_vazio = int((nome_series == "").sum())
    pct_nome_vazio = round((rows_nome_vazio / total) * 100, 2)
    return {"total_rows": total, "rows_nome_vazio": rows_nome_vazio, "pct_nome_vazio": pct_nome_vazio}


def debug_dump(df: pd.DataFrame, debug_records: list, max_rows: int = 120) -> str:
    out = []
    out.append("=" * 120)
    out.append("DEBUG DUMP — CONSTRUÇÃO DOS NOMES (buffer → raw → final)")
    out.append("=" * 120)

    for i, r in enumerate(debug_records[:max_rows], start=1):
        out.append(f"\n[{i}] {r['Item']} | Nº {r['Nº']} | Inciso {r['Inciso']}")
        out.append("-" * 100)

        out.append("Fragmentos coletados (buffer e complementos):")
        if r["Nome_fragments"]:
            for j, frag in enumerate(r["Nome_fragments"], start=1):
                out.append(f"  {j:02d}. {frag}")
        else:
            out.append("  (nenhum fragmento)")

        out.append("\nNome antes do corte:")
        out.append(f"  {r['Nome_raw']}")

        out.append("\nNome FINAL (após terminador do inciso):")
        out.append(f"  {r['Nome_final']}")
        out.append("-" * 100)

    out.append("\nResumo:")
    out.append(f"  Total de registros analisados: {len(debug_records)}")
    out.append(f"  Linhas no DataFrame final:     {len(df)}")
    out.append("=" * 120)

    return "\n".join(out)
