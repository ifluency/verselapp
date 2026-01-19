# parser/parser.py
# -*- coding: utf-8 -*-

import io
import math
import re
from typing import Dict, Any, List, Optional, Tuple

import pdfplumber
import pandas as pd

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas


# ============================================================
# Config / Regras
# ============================================================

INCISO_FONTE = {
    "I": "Compras.gov.br",
    "II": "Contratações similares",
    "III": "Mídias Especializadas",
    "IV": "Fornecedor",
    "V": "Nota Fiscal Eletrônicas",
}

# Exclusões no método de N >= 5
LIMITE_ALTO = 1.25      # v / média_outros > 1.25 => Excessivamente Elevados
LIMITE_BAIXO = 0.75     # v / média_outros < 0.75 => Inexequíveis


# ============================================================
# Utilitários: texto e números (PT-BR)
# ============================================================

def _norm_spaces(s: str) -> str:
    s = s.replace("\u00a0", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _preco_txt_to_float(preco_txt: Any) -> Optional[float]:
    """
    Converte 'R$ 9.309,0000', '9309,0000', '6 750,0000' para float.
    """
    if preco_txt is None:
        return None
    s = str(preco_txt).strip()
    if not s:
        return None

    s = s.replace("R$", "").strip()
    # remove espaços entre dígitos: "6 750,0000" -> "6750,0000"
    s = re.sub(r"(?<=\d)\s+(?=\d)", "", s)
    # PT-BR: milhares '.' e decimal ','
    s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except Exception:
        return None


def _fmt_ptbr(v: Optional[float], casas: int = 4) -> str:
    if v is None:
        return ""
    return f"{v:.{casas}f}".replace(".", ",")


def _coef_var(vals: List[float]) -> Optional[float]:
    if not vals:
        return None
    m = sum(vals) / len(vals)
    if m == 0:
        return None
    var = sum((x - m) ** 2 for x in vals) / len(vals)
    std = math.sqrt(var)
    return std / m


def _median(vals: List[float]) -> Optional[float]:
    if not vals:
        return None
    v = sorted(vals)
    n = len(v)
    mid = n // 2
    if n % 2 == 1:
        return v[mid]
    return (v[mid - 1] + v[mid]) / 2


def _media_sem_o_valor(vals: List[float], idx: int) -> Optional[float]:
    if len(vals) <= 1:
        return None
    return (sum(vals) - vals[idx]) / (len(vals) - 1)


def _audit_item(vals: List[float], upper=LIMITE_ALTO, lower=LIMITE_BAIXO) -> Dict[str, Any]:
    """
    Algoritmo do N >= 5 (baseado no N BRUTO):
    1) Exclui 'Excessivamente Elevados' se v/média_outros > 1.25
    2) Com os mantidos, exclui 'Inexequíveis' se v/média_outros < 0.75
    3) Média final dos restantes
    """
    altos = []
    keep_alto = []
    for i, v in enumerate(vals):
        m = _media_sem_o_valor(vals, i)
        ratio = (v / m) if (m not in (None, 0)) else None
        if ratio is not None and ratio > upper:
            altos.append({"v": v, "m_outros": m, "ratio": ratio})
        else:
            keep_alto.append(v)

    baixos = []
    keep_baixo = []
    for i, v in enumerate(keep_alto):
        m = _media_sem_o_valor(keep_alto, i)
        ratio = (v / m) if (m not in (None, 0)) else None
        if ratio is not None and ratio < lower:
            baixos.append({"v": v, "m_outros": m, "ratio": ratio})
        else:
            keep_baixo.append(v)

    final = keep_baixo[:]
    media_final = (sum(final) / len(final)) if final else None
    cv_final = _coef_var(final) if final else None

    return {
        "iniciais": vals,
        "excluidos_altos": altos,
        "apos_alto": keep_alto,
        "excluidos_baixos": baixos,
        "finais": final,
        "media_final": media_final,
        "cv_final": cv_final,
    }


# ============================================================
# Parsing do PDF (sem coluna Nome)
# ============================================================

_RE_ITEM = re.compile(r"^\s*Item:\s*(\d+)\s*$", re.IGNORECASE)
_RE_CATMAT = re.compile(r"^\s*(\d{6})\s*-\s*")
_RE_HEADER_TABELA = re.compile(r"^\s*N[ºo]\s+Inciso\s+Nome\s+Quantidade\s+Unidade\s+Pre[cç]o\s+unit[aá]rio\s+Data\s+Comp[õo]e\s*$", re.IGNORECASE)

# linha que começa com "N Inciso ..."
_RE_LINHA_INICIO = re.compile(r"^\s*(\d+)\s+(I|II|III|IV|V)\b", re.IGNORECASE)

# detecta final (data + Sim/Não)
_RE_TAIL = re.compile(r"(\d{2}/\d{2}/\d{4})\s+(Sim|Não)\s*$", re.IGNORECASE)


def _try_parse_row(line: str) -> Optional[Dict[str, Any]]:
    """
    Tenta extrair: Nº, Inciso, Quantidade, Preço unitário, Data, Compõe.
    IGNORA Nome e Unidade (não precisamos mais).
    """
    s = _norm_spaces(line)

    # precisa ter (Data + Sim/Não) no final
    m_tail = _RE_TAIL.search(s)
    if not m_tail:
        return None
    data = m_tail.group(1)
    compoe = m_tail.group(2).capitalize()

    # precisa ter preço com R$
    m_price = re.search(r"R\$\s*([\d\.\s]+,\d{4})\s+" + re.escape(data) + r"\s+" + re.escape(compoe) + r"$", s, re.IGNORECASE)
    if not m_price:
        return None
    preco_txt = m_price.group(1)
    preco_txt = re.sub(r"(?<=\d)\s+(?=\d)", "", preco_txt)  # remove espaços internos do número
    preco_txt = preco_txt.strip()

    # começo: Nº + Inciso
    m_start = re.match(r"^\s*(\d+)\s+(I|II|III|IV|V)\b\s*(.*)$", s, re.IGNORECASE)
    if not m_start:
        return None
    n = int(m_start.group(1))
    inciso = m_start.group(2).upper()
    rest = m_start.group(3)

    # rest contém "... Quantidade Unidade R$ ..."
    # vamos cortar antes do "R$"
    idx_rs = rest.lower().rfind("r$")
    if idx_rs == -1:
        return None
    left = rest[:idx_rs].strip()

    # Quantidade: costuma estar imediatamente antes da "Unidade/Embalagem/..." (às vezes colado: 23Unidade)
    m_qty = re.search(r"(\d+)\s*(?:Unidade|Embalagem|Kit|Caixa|Frasco|Pacote|Ampola|Tubo|Rolo|Par|Jogo|Lote)\b", left, re.IGNORECASE)
    if not m_qty:
        # fallback: último número do trecho antes do R$
        m_qty = re.search(r"(\d+)\s*$", left)
    if not m_qty:
        return None
    qtd = int(m_qty.group(1))

    return {
        "Nº": n,
        "Inciso": inciso,
        "Quantidade": qtd,
        "Preço unitário": preco_txt,  # texto por enquanto; convertemos depois para número
        "Data": data,
        "Compõe": compoe,
    }


def _extract_rows_from_pdf(pdf_bytes: bytes) -> pd.DataFrame:
    """
    Extrai linhas de todas as tabelas, por Item, somente Compõe=Sim.
    Retorna DF detalhado (por linha de cotação), SEM Nome e SEM Unidade.
    """
    rows: List[Dict[str, Any]] = []

    cur_item_num: Optional[int] = None
    cur_catmat: Optional[str] = None
    in_table = False

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            txt = page.extract_text(layout=True) or ""
            lines = txt.splitlines()

            i = 0
            while i < len(lines):
                raw = lines[i].strip()

                # identifica início do Item
                m_item = _RE_ITEM.match(raw)
                if m_item:
                    cur_item_num = int(m_item.group(1))
                    cur_catmat = None
                    in_table = False
                    i += 1
                    continue

                # tenta capturar CATMAT no bloco do item
                if cur_item_num is not None and cur_catmat is None:
                    m_cat = _RE_CATMAT.match(raw)
                    if m_cat:
                        cur_catmat = m_cat.group(1)
                        i += 1
                        continue

                # identifica header da tabela
                if _RE_HEADER_TABELA.match(raw):
                    in_table = True
                    i += 1
                    continue

                if in_table and cur_item_num is not None and cur_catmat is not None:
                    # termina tabela se chegar em outro item/legenda
                    if raw.lower().startswith("item:") or raw.lower().startswith("legenda:"):
                        in_table = False
                        i += 1
                        continue

                    # tentamos parsear linha, com possível concatenação de até 3 linhas
                    candidate = raw

                    # se não começa com Nº/Inciso, provavelmente é quebra do "Nome" (irrelevante)
                    if not _RE_LINHA_INICIO.match(candidate):
                        i += 1
                        continue

                    parsed = _try_parse_row(candidate)
                    if parsed is None and i + 1 < len(lines):
                        parsed = _try_parse_row(candidate + " " + lines[i + 1].strip())
                    if parsed is None and i + 2 < len(lines):
                        parsed = _try_parse_row(candidate + " " + lines[i + 1].strip() + " " + lines[i + 2].strip())

                    if parsed is not None:
                        if parsed.get("Compõe") == "Sim":
                            parsed["Item"] = f"Item {cur_item_num}"
                            parsed["CATMAT"] = cur_catmat
                            parsed["Fonte"] = INCISO_FONTE.get(parsed["Inciso"], "")
                            rows.append(parsed)

                    i += 1
                    continue

                i += 1

    if not rows:
        return pd.DataFrame(columns=["Item", "CATMAT", "Nº", "Inciso", "Fonte", "Quantidade", "Preço unitário", "Data", "Compõe"])

    df = pd.DataFrame(rows)

    # Converte preço para número (float) => Excel reconhece como número
    df["Preço unitário"] = df["Preço unitário"].apply(_preco_txt_to_float)

    # garante colunas
    df = df[["Item", "CATMAT", "Nº", "Inciso", "Fonte", "Quantidade", "Preço unitário", "Data", "Compõe"]]

    return df


# ============================================================
# Cálculo final por Item (resumo)
# ============================================================

def _calc_summary(detail_df: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, Dict[str, Any]]]:
    """
    Retorna:
      - summary_df: 1 linha por Item+CATMAT com os campos pedidos
      - audit_map: dicionário com detalhes completos para PDF (memória de cálculo)
    """
    if detail_df is None or detail_df.empty:
        return (
            pd.DataFrame(columns=[
                "Item", "CATMAT",
                "Entradas iniciais", "Entradas finais",
                "Excessivamente Elevados", "Inexequíveis",
                "Coeficiente de variação",
                "Preço Final escolhido",
                "Valor final",
            ]),
            {}
        )

    summary_rows: List[Dict[str, Any]] = []
    audit_map: Dict[str, Dict[str, Any]] = {}

    for (item, catmat), g in detail_df.groupby(["Item", "CATMAT"], sort=False):
        vals_all = [v for v in g["Preço unitário"].tolist() if isinstance(v, (int, float)) and v is not None]
        n_inicial = len(vals_all)

        # AUDIT chave
        audit_key = f"{item} | CATMAT {catmat}"
        audit_map[audit_key] = {
            "Item": item,
            "CATMAT": catmat,
            "N_inicial": n_inicial,
            "Valores_iniciais": vals_all[:],
            "Regra": "",
            "Altos": [],
            "Baixos": [],
            "Valores_finais": [],
            "N_final": 0,
            "CV_final": None,
            "Metodo": "",
            "Valor_final": None,
        }

        if n_inicial == 0:
            summary_rows.append({
                "Item": item,
                "CATMAT": catmat,
                "Entradas iniciais": 0,
                "Entradas finais": 0,
                "Excessivamente Elevados": 0,
                "Inexequíveis": 0,
                "Coeficiente de variação": None,
                "Preço Final escolhido": "",
                "Valor final": None,
            })
            continue

        # Regras pelo N INICIAL (bruto), como você pediu:
        if n_inicial < 5:
            cv = _coef_var(vals_all)
            mean = sum(vals_all) / len(vals_all)
            med = _median(vals_all)
            metodo = "Média" if (cv is not None and cv < 0.25) else "Mediana"
            valor_final = mean if metodo == "Média" else med

            audit_map[audit_key]["Regra"] = "N bruto < 5: CV < 0,25 => Média; senão => Mediana"
            audit_map[audit_key]["Valores_finais"] = vals_all[:]
            audit_map[audit_key]["N_final"] = n_inicial
            audit_map[audit_key]["CV_final"] = cv
            audit_map[audit_key]["Metodo"] = metodo
            audit_map[audit_key]["Valor_final"] = valor_final

            summary_rows.append({
                "Item": item,
                "CATMAT": catmat,
                "Entradas iniciais": n_inicial,
                "Entradas finais": n_inicial,
                "Excessivamente Elevados": 0,
                "Inexequíveis": 0,
                "Coeficiente de variação": cv,
                "Preço Final escolhido": metodo,
                "Valor final": round(valor_final, 2) if valor_final is not None else None,
            })

        else:
            rep = _audit_item(vals_all, upper=LIMITE_ALTO, lower=LIMITE_BAIXO)
            finais = rep["finais"]
            media_final = rep["media_final"]
            cv_final = rep["cv_final"]

            audit_map[audit_key]["Regra"] = "N bruto >= 5: remove altos (>1,25), depois baixos (<0,75), e calcula Média final"
            audit_map[audit_key]["Altos"] = rep["excluidos_altos"]
            audit_map[audit_key]["Baixos"] = rep["excluidos_baixos"]
            audit_map[audit_key]["Valores_finais"] = finais[:]
            audit_map[audit_key]["N_final"] = len(finais)
            audit_map[audit_key]["CV_final"] = cv_final
            audit_map[audit_key]["Metodo"] = "Média"
            audit_map[audit_key]["Valor_final"] = media_final

            summary_rows.append({
                "Item": item,
                "CATMAT": catmat,
                "Entradas iniciais": n_inicial,
                "Entradas finais": len(finais),
                "Excessivamente Elevados": len(rep["excluidos_altos"]),
                "Inexequíveis": len(rep["excluidos_baixos"]),
                "Coeficiente de variação": cv_final,
                "Preço Final escolhido": "Média",
                "Valor final": round(media_final, 2) if media_final is not None else None,
            })

    summary_df = pd.DataFrame(summary_rows)

    # ordena por número do item
    def _item_num(s: str) -> int:
        m = re.search(r"(\d+)", str(s))
        return int(m.group(1)) if m else 999999

    summary_df["__ord"] = summary_df["Item"].apply(_item_num)
    summary_df.sort_values(["__ord", "CATMAT"], inplace=True)
    summary_df.drop(columns=["__ord"], inplace=True)
    summary_df.reset_index(drop=True, inplace=True)

    return summary_df, audit_map


# ============================================================
# PDF: Memória de Cálculo (para TODOS os itens)
# ============================================================

def build_memoria_calculo_pdf_from_audit(audit_map: Dict[str, Dict[str, Any]]) -> bytes:
    """
    Gera um PDF seguindo o padrão do debug, mas para TODOS os itens.
    """
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle("Memória de Cálculo")

    W, H = A4
    left = 15 * mm
    right = 15 * mm
    top = 15 * mm
    bottom = 15 * mm
    line_h = 5.2 * mm

    def new_page():
        c.showPage()
        c.setFont("Helvetica", 10)

    def draw_line(text: str, y: float) -> float:
        max_width = W - left - right
        words = str(text).split(" ")
        cur = ""
        for w in words:
            trial = (cur + " " + w).strip()
            if c.stringWidth(trial, "Helvetica", 10) <= max_width:
                cur = trial
            else:
                c.drawString(left, y, cur)
                y -= line_h
                cur = w
                if y < bottom:
                    new_page()
                    y = H - top
        if cur:
            c.drawString(left, y, cur)
            y -= line_h
        return y

    # título
    c.setFont("Helvetica-Bold", 14)
    c.drawString(left, H - top, "Memória de Cálculo — Auditoria Completa")
    c.setFont("Helvetica", 10)
    y = H - top - 10 * mm

    keys = list(audit_map.keys())

    for k in keys:
        rep = audit_map[k]

        if y < bottom + 40 * mm:
            new_page()
            y = H - top

        c.setFont("Helvetica-Bold", 12)
        y = draw_line(f"{rep['Item']} | CATMAT {rep['CATMAT']} | N inicial = {rep['N_inicial']}", y)

        c.setFont("Helvetica", 10)
        y = draw_line(f"Regra: {rep['Regra']}", y)

        vals_ini = rep.get("Valores_iniciais", [])
        y = draw_line("Valores iniciais:", y)
        y = draw_line(", ".join(_fmt_ptbr(v, 4) for v in vals_ini), y)

        # Se N >= 5, listar exclusões
        altos = rep.get("Altos", [])
        baixos = rep.get("Baixos", [])

        if rep.get("N_inicial", 0) >= 5:
            y = draw_line("--- Exclusões: Excessivamente Elevados (v / média_outros > 1,25) ---", y)
            y = draw_line(f"Qtde: {len(altos)}", y)
            for r in altos:
                y = draw_line(
                    f"v={_fmt_ptbr(r['v'], 4)} | media_outros={_fmt_ptbr(r['m_outros'], 4)} | ratio={_fmt_ptbr(r['ratio'], 4)}",
                    y
                )
                if y < bottom:
                    new_page()
                    y = H - top

            y = draw_line("--- Exclusões: Inexequíveis (v / média_outros < 0,75) ---", y)
            y = draw_line(f"Qtde: {len(baixos)}", y)
            for r in baixos:
                y = draw_line(
                    f"v={_fmt_ptbr(r['v'], 4)} | media_outros={_fmt_ptbr(r['m_outros'], 4)} | ratio={_fmt_ptbr(r['ratio'], 4)}",
                    y
                )
                if y < bottom:
                    new_page()
                    y = H - top

        finais = rep.get("Valores_finais", [])
        y = draw_line("Finais:", y)
        y = draw_line(", ".join(_fmt_ptbr(v, 4) for v in finais), y)
        y = draw_line(f"N final: {rep.get('N_final', 0)}", y)
        y = draw_line(f"CV final: {_fmt_ptbr(rep.get('CV_final'), 6)}", y)

        metodo = rep.get("Metodo", "")
        valor_final = rep.get("Valor_final")
        y = draw_line(f"Preço final escolhido: {metodo}", y)
        y = draw_line(f"Valor final (2 casas): {_fmt_ptbr(valor_final, 2)}", y)

        y -= 3 * mm

    c.showPage()
    c.save()
    return buf.getvalue()


# ============================================================
# API principal
# ============================================================

def validate_extraction(pdf_bytes: bytes) -> bool:
    """
    Validação simples: confirma se parece ser "Relatório de pesquisa de preço".
    """
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            if not pdf.pages:
                return False
            txt = pdf.pages[0].extract_text(layout=True) or ""
            return "Relatório de pesquisa de preço" in txt or "Relatorio de pesquisa de preco" in txt
    except Exception:
        return False


def process_pdf_bytes(pdf_bytes: bytes) -> pd.DataFrame:
    """
    Retorna o DF FINAL (resumo por Item) para exportação em Excel.
    """
    if not validate_extraction(pdf_bytes):
        return pd.DataFrame()

    detail = _extract_rows_from_pdf(pdf_bytes)  # já filtra Compõe=Sim
    summary, _audit_map = _calc_summary(detail)

    # Coeficiente de variação com float (excel reconhece)
    # Valor final (float) (excel reconhece)
    return summary


# ============================================================
# Debug: dump + auditorias
# ============================================================

def debug_dump(pdf_bytes: bytes, pages: int = 3, max_lines: int = 320) -> str:
    """
    Dump de texto das primeiras páginas (layout=True), estilo que você usa no debug.py.
    """
    out = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        n_pages = min(pages, len(pdf.pages))
        for p in range(n_pages):
            out.append("=" * 70)
            out.append(f"PAGE {p+1}")
            out.append("=" * 70)

            txt = pdf.pages[p].extract_text(layout=True) or ""
            lines = txt.splitlines()
            out.append(f"Total linhas extraídas: {len(lines)}")

            for i, line in enumerate(lines[:max_lines]):
                out.append(f"{i:03d} | {line}")

            out.append("")
    return "\n".join(out)


def process_pdf_bytes_debug(pdf_bytes: bytes, audit_first_items: int = 5) -> Tuple[pd.DataFrame, str]:
    """
    Retorna:
      - df_resumo (excel)
      - texto de auditoria (itens com N inicial >= 5) — primeiros N itens
    """
    if not validate_extraction(pdf_bytes):
        return pd.DataFrame(), "Arquivo não validado como Relatório de Pesquisa de Preços."

    detail = _extract_rows_from_pdf(pdf_bytes)
    summary, audit_map = _calc_summary(detail)

    # Auditoria: 5 primeiros itens que tiveram necessidade de cálculo (N inicial >= 5)
    audited = []
    audited.append(f"DEBUG — AUDITORIA DOS CÁLCULOS ({audit_first_items} primeiros itens com N >= 5)")
    audited.append(f"Regras: Excesso se v/média_outros > {LIMITE_ALTO:.2f} | Inexequível se v/média_outros < {LIMITE_BAIXO:.2f}")
    audited.append("")

    count = 0
    for k, rep in audit_map.items():
        if rep.get("N_inicial", 0) >= 5:
            count += 1
            audited.append("=" * 90)
            audited.append(f"{rep['Item']} | N inicial = {rep['N_inicial']}")
            audited.append("Valores iniciais:")
            audited.append(", ".join(_fmt_ptbr(v, 4) for v in rep.get("Valores_iniciais", [])))
            audited.append("")
            audited.append("--- Exclusões: Excessivamente Elevados (v / média_outros > 1.25) ---")
            altos = rep.get("Altos", [])
            audited.append(f"Qtde: {len(altos)}")
            for r in altos:
                audited.append(f"v={_fmt_ptbr(r['v'], 4)} | media_outros={_fmt_ptbr(r['m_outros'], 4)} | ratio={_fmt_ptbr(r['ratio'], 4)}")

            audited.append("")
            audited.append("--- Exclusões: Inexequíveis (v / média_outros < 0.75) ---")
            baixos = rep.get("Baixos", [])
            audited.append(f"Qtde: {len(baixos)}")
            for r in baixos:
                audited.append(f"v={_fmt_ptbr(r['v'], 4)} | media_outros={_fmt_ptbr(r['m_outros'], 4)} | ratio={_fmt_ptbr(r['ratio'], 4)}")

            audited.append("")
            audited.append("Finais:")
            audited.append(", ".join(_fmt_ptbr(v, 4) for v in rep.get("Valores_finais", [])))
            audited.append(f"N final: {rep.get('N_final', 0)}")
            audited.append(f"Média final: {_fmt_ptbr(rep.get('Valor_final'), 4)}")
            audited.append(f"CV final: {_fmt_ptbr(rep.get('CV_final'), 6)}")

            audited.append("")
            if count >= audit_first_items:
                break

    if count == 0:
        audited.append("Nenhum item com N inicial >= 5 foi encontrado.")

    return summary, "\n".join(audited)


def build_memoria_calculo_pdf(pdf_bytes: bytes) -> bytes:
    """
    Gera PDF para TODOS os itens, com base no PDF original (mesma lógica dos cálculos).
    """
    if not validate_extraction(pdf_bytes):
        # PDF mínimo
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=A4)
        c.setTitle("Memória de Cálculo")
        c.setFont("Helvetica", 12)
        c.drawString(15 * mm, 280 * mm, "Memória de Cálculo — arquivo não validado.")
        c.showPage()
        c.save()
        return buf.getvalue()

    detail = _extract_rows_from_pdf(pdf_bytes)
    _summary, audit_map = _calc_summary(detail)
    return build_memoria_calculo_pdf_from_audit(audit_map)
