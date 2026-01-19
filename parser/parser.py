import re
import io
import pdfplumber
import pandas as pd

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4

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




# ===============================
# Memoria de Calculo (PDF)
# ===============================

def _preco_txt_to_float_for_memoria(preco_txt: str):
    if preco_txt is None:
        return None
    s = str(preco_txt).strip().replace("R$", "").strip()
    if not s:
        return None
    # PT-BR: 9.309,0000 -> 9309.0000
    s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except Exception:
        return None


def _coef_var(vals):
    if not vals:
        return None
    mean = sum(vals) / len(vals)
    if mean == 0:
        return None
    var = sum((v - mean) ** 2 for v in vals) / len(vals)
    std = var ** 0.5
    return std / mean


def _audit_item(vals, upper=1.25, lower=0.75):
    """Replica o padrao do /api/debug para um unico item."""
    altos = []
    keep_alto = []
    for i, v in enumerate(vals):
        m = media_sem_o_valor(vals, i)
        ratio = (v / m) if (m not in (None, 0)) else None
        if ratio is not None and ratio > upper:
            altos.append({"v": v, "m_outros": m, "ratio": ratio})
        else:
            keep_alto.append(v)

    baixos = []
    keep_baixo = []
    for i, v in enumerate(keep_alto):
        m = media_sem_o_valor(keep_alto, i)
        ratio = (v / m) if (m not in (None, 0)) else None
        if ratio is not None and ratio < lower:
            baixos.append({"v": v, "m_outros": m, "ratio": ratio})
        else:
            keep_baixo.append(v)

    final = keep_baixo[:]
    return {
        "iniciais": vals,
        "excluidos_altos": altos,
        "apos_alto": keep_alto,
        "excluidos_baixos": baixos,
        "finais": final,
        "media_final": (sum(final) / len(final)) if final else None,
        "cv_final": _coef_var(final) if final else None,
    }


def build_memoria_calculo_txt(df: pd.DataFrame) -> str:
    """Gera um relatorio TXT (monoespacado) com o passo a passo dos calculos para TODOS os itens."""
    if df is None or getattr(df, "empty", True):
        return "DF vazio. Nenhuma linha encontrada.\n"


    required = {"Item", "Preço unitário"}
    missing = [c for c in required if c not in df.columns]
    if missing:
        return f"Colunas esperadas ausentes: {missing}. Colunas encontradas: {list(df.columns)}\n"

    out = []
    out.append("MEMORIA DE CALCULO — AUDITORIA DOS PRECOS")
    out.append("Regras aplicadas:")
    out.append(" - Se N < 5: CV decide media/mediana (CV < 0,25 -> media; senao -> mediana)")
    out.append(" - Se N >= 5: filtro por ratio e media")
    out.append("    - Excessivamente Elevados: v / media_outros > 1,25")
    out.append("    - Inexequiveis: v / media_outros < 0,75 (apos remover elevados)")
    out.append("")

    for item, g_raw in df.groupby("Item", sort=False):
        out.append("=" * 110)
        out.append(str(item))

        g = g_raw.copy()
        g["preco_num"] = g["Preço unitário"].apply(_preco_txt_to_float_for_memoria)
        vals = g["preco_num"].dropna().astype(float).tolist()

        n_bruto = len(g_raw)
        n_parse = len(vals)
        out.append(f"N bruto (linhas): {n_bruto} | N precos parseados: {n_parse}")

        if n_parse == 0:
            out.append("⚠️ Nenhum preco conseguiu ser convertido para numero.")
            out.append("Valores originais da coluna 'Preço unitário' (primeiros 50):")
            out.append(", ".join([str(x) for x in g_raw["Preço unitário"].tolist()[:50]]))
            out.append("")
            continue

        # Caso com poucos valores
        if n_parse == 1:
            out.append("⚠️ Apenas 1 preco numerico. Nao e possivel aplicar CV nem filtros.")
            out.append(f"Valor unico: {vals[0]:.4f}")
            out.append("Preco Final escolhido: valor unico")
            out.append(f"Valor escolhido: {float_to_preco_txt(vals[0], decimals=2)}")
            out.append("")
            continue

        # N < 5 -> CV decide
        if n_parse < 5:
            cv = _coef_var(vals)
            mean = sum(vals) / len(vals)
            med = float(pd.Series(vals).median())
            out.append("Valores (iniciais parseados):")
            out.append(", ".join([f"{v:.4f}" for v in vals]))
            out.append("")
            out.append(f"Media: {mean:.4f}")
            out.append(f"Mediana: {med:.4f}")
            out.append(f"CV: {'' if cv is None else f'{cv:.6f}'}")

            if cv is None:
                escolhido = "Mediana"
                valor = med
                motivo = "CV indefinido (media=0)"
            elif cv < 0.25:
                escolhido = "Media"
                valor = mean
                motivo = "CV < 0,25"
            else:
                escolhido = "Mediana"
                valor = med
                motivo = "CV >= 0,25"

            out.append(f"Decisao: {escolhido} ({motivo})")
            out.append(f"Valor escolhido (2 casas): {float_to_preco_txt(valor, decimals=2)}")
            out.append("")
            continue

        # N >= 5 -> filtro e media
        rep = _audit_item(vals, upper=1.25, lower=0.75)

        out.append("Valores (iniciais parseados):")
        out.append(", ".join([f"{v:.4f}" for v in rep["iniciais"]]))
        out.append("")

        out.append("--- Exclusoes: Excessivamente Elevados (v / media_outros > 1,25) ---")
        out.append(f"Qtde: {len(rep['excluidos_altos'])}")
        for r in rep["excluidos_altos"]:
            out.append(f"v={r['v']:.4f} | media_outros={r['m_outros']:.4f} | ratio={r['ratio']:.4f}")
        out.append("")

        out.append("Apos ALTO (mantidos):")
        out.append(", ".join([f"{v:.4f}" for v in rep["apos_alto"]]))
        out.append("")

        out.append("--- Exclusoes: Inexequiveis (v / media_outros < 0,75) ---")
        out.append(f"Qtde: {len(rep['excluidos_baixos'])}")
        for r in rep["excluidos_baixos"]:
            out.append(f"v={r['v']:.4f} | media_outros={r['m_outros']:.4f} | ratio={r['ratio']:.4f}")
        out.append("")

        out.append("Finais:")
        out.append(", ".join([f"{v:.4f}" for v in rep["finais"]]))
        out.append(f"N final: {len(rep['finais'])}")
        media_txt = "" if rep["media_final"] is None else f"{rep['media_final']:.4f}"
        out.append(f"Media final: {media_txt}")
        cv_txt = "" if rep["cv_final"] is None else f"{rep['cv_final']:.6f}"
        out.append(f"CV final: {cv_txt}")

        valor2 = rep["media_final"]
        out.append("Preco Final escolhido: Media")
        val_txt = float_to_preco_txt(valor2, decimals=2) if valor2 is not None else ""
        out.append(f"Valor escolhido (2 casas): {val_txt}")
        out.append("")

    return "\n".join(out) + "\n"



def _text_to_pdf_bytes(text: str) -> bytes:
    """Renderiza um TXT monoespacado em PDF com quebra de pagina."""
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)

    width, height = A4
    left = 36
    right = 36
    top = 36
    bottom = 36
    usable_width = width - left - right

    font_name = "Courier"
    font_size = 9
    line_height = 11
    c.setFont(font_name, font_size)

    y = height - top

    # Heuristica de quebra de linha por largura
    # Courier ~ monoespacado: estimativa de caracteres por linha
    avg_char_w = c.stringWidth("M", font_name, font_size)
    max_chars = max(20, int(usable_width // avg_char_w))

    def draw_line(s: str):
        nonlocal y
        if y <= bottom:
            c.showPage()
            c.setFont(font_name, font_size)
            y = height - top
        c.drawString(left, y, s)
        y -= line_height

    for raw_line in (text or "").splitlines():
        line = raw_line.rstrip("\n")
        if len(line) <= max_chars:
            draw_line(line)
        else:
            # quebra simples por caracteres
            start = 0
            while start < len(line):
                draw_line(line[start : start + max_chars])
                start += max_chars

    c.save()
    buffer.seek(0)
    return buffer.read()


def build_memoria_calculo_pdf_bytes(df: pd.DataFrame) -> bytes:
    """Gera o PDF 'Memoria de Calculo' (bytes) a partir do dataframe final (Compõe=Sim)."""
    txt = build_memoria_calculo_txt(df)
    return _text_to_pdf_bytes(txt)
