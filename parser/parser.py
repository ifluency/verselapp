import re
import io
import pdfplumber
import pandas as pd

RE_ITEM = re.compile(r"^Item:\s*(\d+)\b", re.IGNORECASE)
RE_CATMAT = re.compile(r"(\d{6})\s*-\s*")

RE_PAGE_MARK = re.compile(r"^\s*\d+\s+de\s+\d+\s*$", re.IGNORECASE)
RE_DATE_TOKEN = re.compile(r"^\d{2}/\d{2}/\d{4}$")
RE_ROW_START = re.compile(r"^\s*(\d+)\s+([IVX]+)\b", re.IGNORECASE)

INCISO_TO_FONTE = {
    "I": "Compras.gov.br",
    "II": "Contratações similares",
    "III": "Mídias Especializadas",
    "IV": "Fornecedor",
    "V": "Nota Fiscal Eletrônicas",
}

FINAL_COLUMNS = [
    "Item",
    "CATMAT",
    "Nº",
    "Inciso",
    "Fonte",
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
    # Compras.gov. br -> Compras.gov.br
    s = re.sub(r"(Compras\.gov\.)\s*(br)\b", r"\1\2", s, flags=re.IGNORECASE)

    # “110Unidade” -> “110 Unidade”
    s = re.sub(r"(\d)([A-Za-zÀ-ÿ])", r"\1 \2", s)
    s = re.sub(r"([A-Za-zÀ-ÿ])(\d)", r"\1 \2", s)

    # R$ com espaço
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


def parse_row_fields(row_line: str):
    """
    Parseia a linha do registro (sem nome):
      "4 I 110 Unidade R$ 150,4500 05/12/2025 Sim"
    Retorna campos da tabela (sem Nome).
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

    # preço cru (sem R$)
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
        if re.fullmatch(r"\d+(?:[.,]\d+)?", toks[i]) and (
            toks[i + 1].lower().startswith("unidade") or toks[i + 1].lower().startswith("embalagem")
        ):
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


def preco_txt_to_float(preco_txt: str) -> float | None:
    if preco_txt is None:
        return None
    s = str(preco_txt).strip()
    if not s:
        return None
    s = s.replace("R$", "").strip()
    s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except Exception:
        return None


def float_to_preco_txt(x: float | None, decimals: int = 2) -> str:
    if x is None:
        return ""
    s = f"{x:.{decimals}f}"
    return s.replace(".", ",")


def coeficiente_variacao(vals: list[float]) -> float | None:
    if not vals:
        return None
    mean = sum(vals) / len(vals)
    if mean == 0:
        return None
    var = sum((v - mean) ** 2 for v in vals) / len(vals)  # ddof=0
    std = var ** 0.5
    return std / mean


def media_sem_o_valor(vals: list[float], idx: int) -> float | None:
    if len(vals) <= 1:
        return None
    s = sum(vals) - vals[idx]
    return s / (len(vals) - 1)


def filtrar_outliers_por_ratio(vals: list[float], upper: float = 1.25, lower: float = 0.75):
    """
    Retorna:
      - vals_final: lista final após filtros
      - excluidos_alto: quantos foram removidos por excessivamente elevados
      - excluidos_baixo: quantos foram removidos por inexequíveis
    Regras:
      - alto: remove se (v / média(outros)) > upper
      - baixo: após remover altos, remove se (v / média(outros)) < lower
    """
    if len(vals) < 2:
        return vals[:], 0, 0

    # PASSO alto
    keep_alto = []
    excl_alto = 0
    for i, v in enumerate(vals):
        m = media_sem_o_valor(vals, i)
        if m is None or m == 0:
            keep_alto.append(v)
            continue
        ratio = v / m
        if ratio <= upper:
            keep_alto.append(v)
        else:
            excl_alto += 1

    if len(keep_alto) < 2:
        return keep_alto, excl_alto, 0

    # PASSO baixo
    keep_baixo = []
    excl_baixo = 0
    for i, v in enumerate(keep_alto):
        m = media_sem_o_valor(keep_alto, i)
        if m is None or m == 0:
            keep_baixo.append(v)
            continue
        ratio = v / m
        if ratio >= lower:
            keep_baixo.append(v)
        else:
            excl_baixo += 1

    return keep_baixo, excl_alto, excl_baixo


def gerar_resumo(df: pd.DataFrame) -> pd.DataFrame:
    cols = [
        "Item",
        "CATMAT",
        "Número de entradas iniciais",
        "Número de entradas finais",
        "Excessivamente Elevados",
        "Inexequíveis",
        "Coeficiente de variação",
        "Preço Final escolhido",
        "Valor escolhido",
    ]

    if df is None or df.empty:
        return pd.DataFrame(columns=cols)

    if "Preço unitário" not in df.columns:
        raise ValueError("Coluna 'Preço unitário' não encontrada no dataframe.")

    df_calc = df.copy()
    df_calc["preco_num"] = df_calc["Preço unitário"].apply(preco_txt_to_float)
    df_calc = df_calc[df_calc["preco_num"].notna()].copy()

    rows = []
    for item, g in df_calc.groupby("Item", sort=False):
        catmat = g["CATMAT"].dropna().iloc[0] if g["CATMAT"].notna().any() else ""
        vals = g["preco_num"].astype(float).tolist()
        n_inicial = len(vals)

        excl_alto = 0
        excl_baixo = 0

        if n_inicial < 5:
            cv = coeficiente_variacao(vals)
            mean = sum(vals) / len(vals) if vals else None
            med = float(pd.Series(vals).median()) if vals else None

            if cv is None:
                escolhido = "Mediana"
                valor = med
            else:
                if cv < 0.25:
                    escolhido = "Média"
                    valor = mean
                else:
                    escolhido = "Mediana"
                    valor = med

            n_final = n_inicial
            cv_final = cv

        else:
            vals_filtrados, excl_alto, excl_baixo = filtrar_outliers_por_ratio(vals, upper=1.25, lower=0.75)
            n_final = len(vals_filtrados)
            valor = (sum(vals_filtrados) / n_final) if n_final > 0 else None
            escolhido = "Média"
            cv_final = coeficiente_variacao(vals_filtrados) if n_final > 0 else None

        rows.append(
            {
                "Item": item,
                "CATMAT": catmat,
                "Número de entradas iniciais": n_inicial,
                "Número de entradas finais": n_final,
                "Excessivamente Elevados": excl_alto,
                "Inexequíveis": excl_baixo,
                "Coeficiente de variação": (round(cv_final, 6) if cv_final is not None else ""),
                "Preço Final escolhido": escolhido,
                "Valor escolhido": float_to_preco_txt(valor, decimals=2),
            }
        )

    return pd.DataFrame(rows, columns=cols)


def process_pdf_bytes_debug(pdf_bytes: bytes) -> tuple[pd.DataFrame, list[dict]]:
    records: list[dict] = []
    debug_records: list[dict] = []

    current_item = None
    current_catmat = None
    capture = False

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
                    capture = True
                    continue
                if is_table_off(line):
                    capture = False
                    continue
                if not capture:
                    continue

                s = normalize_text(line)
                if is_header(s):
                    continue

                # linha do registro
                if RE_ROW_START.match(s):
                    fields = parse_row_fields(s)
                    if not fields:
                        continue

                    inciso = fields["Inciso"]
                    fonte = INCISO_TO_FONTE.get(inciso, "")

                    row = {
                        "Item": f"Item {current_item}" if current_item is not None else None,
                        "CATMAT": current_catmat,
                        "Nº": fields["Nº"],
                        "Inciso": inciso,
                        "Fonte": fonte,
                        "Quantidade": fields["Quantidade"],
                        "Preço unitário": fields["Preço unitário"],
                        "Data": fields["Data"],
                        "Compõe": fields["Compõe"],
                    }
                    records.append(row)
                    debug_records.append(row.copy())

    df = pd.DataFrame(records, columns=FINAL_COLUMNS)

    # somente Compõe=Sim
    if "Compõe" in df.columns:
        df = df[df["Compõe"] == "Sim"].copy()

    df.reset_index(drop=True, inplace=True)

    # garante colunas
    for col in FINAL_COLUMNS:
        if col not in df.columns:
            df[col] = None
    df = df[FINAL_COLUMNS]

    return df, debug_records


def process_pdf_bytes(pdf_bytes: bytes) -> pd.DataFrame:
    df, _ = process_pdf_bytes_debug(pdf_bytes)

    # (opcional) gerar resumo aqui se você quiser no parse.py; mas deixo só o DF "Dados"
    return df


def validate_extraction(df: pd.DataFrame) -> dict:
    return {"total_rows": int(len(df)) if df is not None else 0}


def debug_dump(df: pd.DataFrame, debug_records: list[dict], max_rows: int = 200) -> str:
    out = []
    out.append("=" * 120)
    out.append("DEBUG DUMP — REGISTROS EXTRAÍDOS (sem coluna Nome; com Fonte)")
    out.append("=" * 120)

    for i, r in enumerate(debug_records[:max_rows], start=1):
        out.append(
            f"[{i:03d}] {r.get('Item')} | CATMAT {r.get('CATMAT')} | Nº {r.get('Nº')} | "
            f"Inciso {r.get('Inciso')} | Fonte {r.get('Fonte')} | "
            f"Qtd {r.get('Quantidade')} | Preço {r.get('Preço unitário')} | "
            f"Data {r.get('Data')} | Compõe {r.get('Compõe')}"
        )

    out.append("")
    out.append(f"Total registros parseados (antes do filtro Compõe=Sim): {len(debug_records)}")
    out.append(f"Total linhas no DF final (Compõe=Sim): {len(df) if df is not None else 0}")
    out.append("=" * 120)
    return "\n".join(out)
