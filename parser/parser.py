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

# Regras fixas que você passou:
INCISO_TERMINATORS = {
    "I": "Compras.gov.br",
    "III": "Mídias Especializadas",
    "IV": "Fornecedor",
    # II e V: sem terminador fixo por enquanto (podemos adicionar depois)
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

    if low in ("br", "gov.br"):
        return True
    if "compras.gov" in low or "gov.br" in low:
        return True

    letters = sum(ch.isalpha() for ch in s)
    digits = sum(ch.isdigit() for ch in s)
    if letters >= 6 and digits <= 1:
        return True

    return False


def parse_row_fields(row_line: str):
    """
    Parseia a linha principal do registro:
      '4 I 110 Unidade R$ 150,4500 05/12/2025 Sim'
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

    # parte do nome dentro da própria linha (normalmente vazio no seu dump, mas mantém)
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


def cut_name_by_inciso(nome_raw: str, inciso: str) -> str:
    """
    Corta o nome exatamente no terminador do inciso quando existir.
    Isso evita 'vazamento' do nome do próximo registro.
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


def process_pdf_bytes_debug(pdf_bytes: bytes) -> tuple[pd.DataFrame, list]:
    """
    Retorna:
      df_final (FINAL_COLUMNS)
      debug_records (lista com fragmentos, raw, final)
    """
    records = []
    debug_records = []

    current_item = None
    current_catmat = None
    capture = False

    pending_name = ""      # prefixo do próximo
    current_fields = None
    current_name = ""      # nome em construção do registro atual
    nome_fragments = []    # fragmentos do nome (para debug)

    def finalize_current():
        nonlocal current_fields, current_name, pending_name, nome_fragments

        if not current_fields:
            return

        inciso = current_fields["Inciso"]
        nome_raw = normalize_text(clean_spaces(current_name))
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
            "Nome_fragments": nome_fragments.copy(),
            "Nome_raw": nome_raw,
            "Nome_final": nome_final,
        })

        current_fields = None
        current_name = ""
        nome_fragments = []

    def add_to_pending(fragment: str):
        nonlocal pending_name
        pending_name = clean_spaces((pending_name + " " + fragment).strip())

    def add_to_current(fragment: str):
        nonlocal current_name, nome_fragments
        fragment = normalize_text(fragment)
        current_name = clean_spaces((current_name + " " + fragment).strip())
        nome_fragments.append(fragment)

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
                    pending_name = ""
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
                if is_header(s):
                    continue

                # linha principal do registro
                if RE_ROW_START.match(s):
                    finalize_current()

                    fields = parse_row_fields(s)
                    if not fields:
                        if looks_like_name_fragment(s):
                            add_to_pending(s)
                        continue

                    current_fields = fields

                    # Nome inicial = pending + inline (se existir)
                    parts = []
                    nome_fragments = []

                    if pending_name:
                        parts.append(pending_name)
                        nome_fragments.append(normalize_text(pending_name))

                    if fields.get("InlineNome"):
                        parts.append(fields["InlineNome"])
                        nome_fragments.append(normalize_text(fields["InlineNome"]))

                    current_name = clean_spaces(" ".join(parts))
                    pending_name = ""
                    continue

                # fragmento de nome
                if looks_like_name_fragment(s):
                    if not current_fields:
                        add_to_pending(s)
                        continue

                    low = s.lower()
                    name_has_gov = contains_gov(current_name)

                    # 1) "br" / "gov.br" sempre anexa
                    if low in ("br", "gov.br"):
                        add_to_current(s)
                        continue

                    # 2) se nome atual ainda não tem gov e fragmento tem gov, anexa
                    if (not name_has_gov) and contains_gov(s):
                        add_to_current(s)
                        continue

                    # 3) caso contrário é do próximo -> finaliza e joga pra pending
                    finalize_current()
                    add_to_pending(s)
                    continue

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


def debug_dump(df: pd.DataFrame, debug_records: list, max_rows: int = 50) -> str:
    """
    Retorna uma STRING (bom pra endpoint /api/debug),
    mostrando fragmentos -> raw -> final.
    """
    out = []
    out.append("=" * 120)
    out.append("DEBUG DUMP — CONSTRUÇÃO DOS NOMES")
    out.append("=" * 120)

    for i, r in enumerate(debug_records[:max_rows], start=1):
        out.append(f"\n[{i}] {r['Item']} | Nº {r['Nº']} | Inciso {r['Inciso']}")
        out.append("-" * 100)

        out.append("Fragmentos coletados:")
        if r["Nome_fragments"]:
            for j, frag in enumerate(r["Nome_fragments"], start=1):
                out.append(f"  {j:02d}. {frag}")
        else:
            out.append("  (nenhum fragmento)")

        out.append("\nNome antes do corte:")
        out.append(f"  {r['Nome_raw']}")

        out.append("\nNome FINAL:")
        out.append(f"  {r['Nome_final']}")
        out.append("-" * 100)

    out.append("\nResumo:")
    out.append(f"  Total de registros analisados: {len(debug_records)}")
    out.append(f"  Linhas no DataFrame final:     {len(df)}")
    out.append("=" * 120)

    return "\n".join(out)
