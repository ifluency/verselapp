import io
import re
from typing import Optional, List, Dict

import pdfplumber
import pandas as pd


INCISO_FONTE = {
    "I": "Compras.gov.br",
    "II": "Contratações similares",
    "III": "Mídias Especializadas",
    "IV": "Fornecedor",
    "V": "Nota Fiscal Eletrônicas",
}


def preco_txt_to_float_ptbr(preco_txt: str) -> Optional[float]:
    """
    Converte '150,4500' ou 'R$ 9.309,0000' ou '6 750,0000' em float (7107.6644 etc),
    sempre aceitando vírgula como decimal.
    """
    if preco_txt is None:
        return None
    s = str(preco_txt).strip()
    if not s:
        return None

    s = s.replace("R$", "").strip()

    # remove espaços no meio de números tipo "6 750,0000"
    s = re.sub(r"(?<=\d)\s+(?=\d)", "", s)

    # remove milhares com ponto e troca vírgula por ponto
    s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except Exception:
        return None


def coef_var(vals: List[float]) -> Optional[float]:
    if not vals:
        return None
    m = sum(vals) / len(vals)
    if m == 0:
        return None
    var = sum((v - m) ** 2 for v in vals) / len(vals)
    std = var ** 0.5
    return std / m


def media_sem_o_valor(vals: List[float], idx: int) -> Optional[float]:
    if len(vals) <= 1:
        return None
    return (sum(vals) - vals[idx]) / (len(vals) - 1)


def audit_item(vals: List[float], upper=1.25, lower=0.75) -> Dict:
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
        "cv_final": coef_var(final) if final else None,
    }


def _extract_item_catmat(lines: List[str], start_idx: int) -> (Optional[str], Optional[str]):
    """
    Acha:
    - Item: "Item: 1"
    - CATMAT: "456410 - ..."
    """
    item = None
    catmat = None

    # procura "Item:" nas linhas próximas
    for j in range(max(0, start_idx - 40), min(len(lines), start_idx + 10)):
        m = re.search(r"\bItem:\s*(\d+)\b", lines[j])
        if m:
            item = f"Item {m.group(1)}"
            break

    # procura CATMAT (6 dígitos antes de " - ")
    for j in range(start_idx, min(len(lines), start_idx + 50)):
        m = re.search(r"\b(\d{6})\s*-\s*", lines[j])
        if m:
            catmat = m.group(1)
            break

    return item, catmat


def _looks_like_table_header(line: str) -> bool:
    return "Nº" in line and "Inciso" in line and "Preço unitário" in line and "Compõe" in line


def _parse_table_rows(lines: List[str], header_idx: int, end_idx: int) -> List[Dict]:
    """
    Lê linhas depois do header e monta registros:
    Colunas esperadas: Nº | Inciso | (Nome - removido) | Quantidade | Unidade | Preço | Data | Compõe
    Mas o nome a gente ignora no resultado final.
    """
    rows = []

    i = header_idx + 1
    buffer = []

    def flush_buffer(buf: List[str]):
        # Junta e tenta extrair campos com regex robusto
        # Observação: Nome pode quebrar em várias linhas, mas vamos ignorar
        # Vamos pegar:
        # Nº (int)
        # Inciso (I/II/III/IV/V)
        # Quantidade (int)
        # Preço (ex: R$ 150,4500)
        # Data (dd/mm/yyyy)
        # Compõe (Sim/Não)
        # Unidade: frequentemente "Unidade" ou "Embalagem" etc. (aqui não precisamos mais)

        text = " ".join([t.strip() for t in buf if t.strip()])
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            return

        # Ex:
        # "4 I EMPRESA ... 110Unidade R$ 150,4500 05/12/2025 Sim"
        # Pode vir "110 Unidade" ou "110Unidade"
        # Vamos permitir ambos.

        m = re.search(
            r"^\s*(\d+)\s+"
            r"(I{1,3}|IV|V)\s+"
            r".*?\s"
            r"(\d+)\s*"
            r"(?:Unidade|Embalagem|Kit|Pacote|Frasco|Caixa|Unidades)?\s*"
            r"R\$\s*([0-9\.\s]+,[0-9]{2,4})\s+"
            r"(\d{2}/\d{2}/\d{4})\s+"
            r"(Sim|Não)\s*$",
            text,
            flags=re.IGNORECASE,
        )

        if not m:
            return

        n = int(m.group(1))
        inciso = m.group(2).upper()
        qtd = int(m.group(3))
        preco = m.group(4).strip()
        data = m.group(5)
        compoe = "Sim" if m.group(6).lower().startswith("sim") else "Não"

        rows.append(
            {
                "Nº": n,
                "Inciso": inciso,
                "Quantidade": qtd,
                "Preço unitário": preco,
                "Data": data,
                "Compõe": compoe,
                "Fonte": INCISO_FONTE.get(inciso, ""),
            }
        )

    while i < end_idx:
        line = lines[i].strip()

        # Para quando começa outra seção
        if re.search(r"\bLegenda:\b", line) or re.search(r"\bItem:\s*\d+\b", line):
            break

        # Se começa com número + inciso, inicia novo buffer
        if re.match(r"^\d+\s+(I{1,3}|IV|V)\b", line):
            if buffer:
                flush_buffer(buffer)
                buffer = []
            buffer.append(line)
        else:
            # continuação do "Nome" quebrado / ou partes soltas como "gov.br"
            if buffer:
                buffer.append(line)
            else:
                # se não tem buffer ainda, ignora
                pass

        i += 1

    if buffer:
        flush_buffer(buffer)

    return rows


def process_pdf_bytes(pdf_bytes: bytes) -> Optional[pd.DataFrame]:
    """
    Retorna DF filtrado Compõe=Sim, sem coluna Nome.
    """
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            all_rows = []
            current_item = None
            current_catmat = None

            for page in pdf.pages:
                txt = page.extract_text(layout=True) or ""
                lines = txt.splitlines()

                # atualiza item/catmat se encontrar "Item:" nesta página
                for idx, line in enumerate(lines):
                    m_item = re.search(r"\bItem:\s*(\d+)\b", line)
                    if m_item:
                        current_item = f"Item {m_item.group(1)}"
                        # tenta achar catmat logo após
                        for j in range(idx, min(len(lines), idx + 60)):
                            m_cat = re.search(r"\b(\d{6})\s*-\s*", lines[j])
                            if m_cat:
                                current_catmat = m_cat.group(1)
                                break

                # procura header da tabela
                for idx, line in enumerate(lines):
                    if _looks_like_table_header(line):
                        # parse até o fim da página
                        rows = _parse_table_rows(lines, idx, len(lines))
                        for r in rows:
                            r["Item"] = current_item or ""
                            r["CATMAT"] = current_catmat or ""
                            all_rows.append(r)

            if not all_rows:
                return pd.DataFrame([])

            df = pd.DataFrame(all_rows)

            # mantém apenas Compõe = Sim
            if "Compõe" in df.columns:
                df = df[df["Compõe"].str.strip().str.lower() == "sim"].copy()
            else:
                # se não tem coluna, não retorna nada
                return pd.DataFrame([])

            # Reordena e remove colunas não desejadas
            cols = ["Item", "CATMAT", "Nº", "Inciso", "Fonte", "Quantidade", "Preço unitário", "Data", "Compõe"]
            df = df[[c for c in cols if c in df.columns]].copy()

            # Preço unitário: mantemos texto com vírgula (como você pediu),
            # mas vamos limpar "R$" caso tenha escapado
            df["Preço unitário"] = df["Preço unitário"].astype(str).str.replace("R$", "", regex=False).str.strip()
            df["Preço unitário"] = df["Preço unitário"].str.replace(r"\s+", "", regex=True)  # remove espaços no meio

            return df.reset_index(drop=True)

    except Exception:
        # Evita traceback no frontend
        return pd.DataFrame([])


def build_debug_audit_text(df: pd.DataFrame, max_items=5, min_n=5) -> str:
    if df is None or df.empty:
        return "DF vazio. Nenhuma linha encontrada.\n"

    if "Item" not in df.columns or "Preço unitário" not in df.columns:
        return f"Colunas insuficientes: {list(df.columns)}\n"

    out = []
    out.append("DEBUG — AUDITORIA DOS CÁLCULOS (5 primeiros itens com N bruto >= 5)")
    out.append(f"Critério: N bruto >= {min_n}")
    out.append("Regras: Excesso se v/média_outros > 1.25 | Inexequível se v/média_outros < 0.75")
    out.append("")

    shown = 0
    for item, g_raw in df.groupby("Item", sort=False):
        n_bruto = len(g_raw)
        if n_bruto < min_n:
            continue

        vals = [preco_txt_to_float_ptbr(x) for x in g_raw["Preço unitário"].tolist()]
        vals = [v for v in vals if v is not None]
        n_parse = len(vals)

        out.append("=" * 90)
        out.append(f"{item} | N bruto = {n_bruto} | N preços parseados = {n_parse}")

        if n_parse < 2:
            out.append("⚠️ Poucos preços parseados para auditoria (precisa >= 2).")
            out.append("Preços originais:")
            out.append(", ".join([str(x) for x in g_raw["Preço unitário"].tolist()[:50]]))
            out.append("")
            shown += 1
            if shown >= max_items:
                break
            continue

        rep = audit_item(vals)

        out.append("Valores iniciais (parseados):")
        out.append(", ".join([f"{v:.4f}" for v in rep["iniciais"]]))
        out.append("")
        out.append("--- Exclusões: Excessivamente Elevados ---")
        out.append(f"Qtde: {len(rep['excluidos_altos'])}")
        for r in rep["excluidos_altos"]:
            out.append(f"v={r['v']:.4f} | media_outros={r['m_outros']:.4f} | ratio={r['ratio']:.4f}")
        out.append("")
        out.append("--- Exclusões: Inexequíveis ---")
        out.append(f"Qtde: {len(rep['excluidos_baixos'])}")
        for r in rep["excluidos_baixos"]:
            out.append(f"v={r['v']:.4f} | media_outros={r['m_outros']:.4f} | ratio={r['ratio']:.4f}")
        out.append("")
        out.append(f"N final: {len(rep['finais'])}")
        out.append(f"Média final: {rep['media_final']:.4f}" if rep["media_final"] is not None else "Média final: ")
        out.append(f"CV final: {rep['cv_final']:.6f}" if rep["cv_final"] is not None else "CV final: ")
        out.append("")

        shown += 1
        if shown >= max_items:
            break

    if shown == 0:
        out.append("Nenhum item com N bruto >= 5 encontrado.")

    return "\n".join(out) + "\n"


def build_memoria_calculo_text(df: pd.DataFrame) -> str:
    """
    Memória de cálculo para TODOS os itens (independente de N).
    Explica:
    - N bruto
    - valores parseados
    - regra aplicada (<5: CV decide Média/Mediana; >=5: filtros alto/baixo e Média final)
    - CV final
    - contagem excluídos
    """
    if df is None or df.empty:
        return "Memória de cálculo vazia: nenhum dado extraído.\n"

    out = []
    out.append("MEMÓRIA DE CÁLCULO — Relatório de pesquisa de preço (Compras.gov.br)")
    out.append("Observação: 'Preço unitário' foi convertido para número usando regra PT-BR (vírgula decimal).")
    out.append("Regras:")
    out.append(" - Se N bruto < 5: calcula CV. Se CV < 0.25 => Média; senão => Mediana.")
    out.append(" - Se N bruto >= 5: remove Excessivamente Elevados (v/média_outros > 1.25), depois remove Inexequíveis (v/média_outros < 0.75).")
    out.append(" - Resultado final (N>=5): Média dos valores restantes.")
    out.append("")

    for item, g_raw in df.groupby("Item", sort=False):
        n_bruto = len(g_raw)

        vals_txt = g_raw["Preço unitário"].tolist()
        vals = [preco_txt_to_float_ptbr(x) for x in vals_txt]
        vals_num = [v for v in vals if v is not None]

        out.append("=" * 110)
        out.append(f"{item} | CATMAT: {str(g_raw['CATMAT'].iloc[0]) if 'CATMAT' in g_raw.columns and len(g_raw) else ''}")
        out.append(f"N bruto (linhas): {n_bruto} | N preços parseados: {len(vals_num)}")
        out.append("Preços (texto):")
        out.append(", ".join([str(x) for x in vals_txt]))
        out.append("Preços (num):")
        out.append(", ".join([f"{v:.4f}" for v in vals_num]) if vals_num else "(nenhum preço parseado)")
        out.append("")

        if len(vals_num) < 2:
            out.append("⚠️ Não há preços numéricos suficientes para cálculo (precisa >= 2).")
            out.append("")
            continue

        if n_bruto < 5:
            cv = coef_var(vals_num)
            # média e mediana
            mean = sum(vals_num) / len(vals_num)
            med = float(pd.Series(vals_num).median())

            if cv is None:
                criterio = "Mediana"
                valor = med
            else:
                criterio = "Média" if cv < 0.25 else "Mediana"
                valor = mean if criterio == "Média" else med

            out.append("TIPO: N bruto < 5")
            out.append(f"CV: {cv:.6f}" if cv is not None else "CV: ")
            out.append(f"Escolha: {criterio}")
            out.append(f"Valor final: {valor:.2f}")
            out.append("")
        else:
            rep = audit_item(vals_num)
            out.append("TIPO: N bruto >= 5")
            out.append("--- Exclusões: Excessivamente Elevados ---")
            out.append(f"Qtde: {len(rep['excluidos_altos'])}")
            for r in rep["excluidos_altos"]:
                out.append(f"v={r['v']:.4f} | media_outros={r['m_outros']:.4f} | ratio={r['ratio']:.4f}")

            out.append("")
            out.append("--- Exclusões: Inexequíveis ---")
            out.append(f"Qtde: {len(rep['excluidos_baixos'])}")
            for r in rep["excluidos_baixos"]:
                out.append(f"v={r['v']:.4f} | media_outros={r['m_outros']:.4f} | ratio={r['ratio']:.4f}")

            out.append("")
            out.append("Valores finais:")
            out.append(", ".join([f"{v:.4f}" for v in rep["finais"]]))
            out.append(f"N final: {len(rep['finais'])}")
            out.append(f"Média final: {rep['media_final']:.4f}" if rep["media_final"] is not None else "Média final: ")
            out.append(f"CV final: {rep['cv_final']:.6f}" if rep["cv_final"] is not None else "CV final: ")
            out.append(f"Valor final (2 casas): {(rep['media_final'] if rep['media_final'] is not None else 0):.2f}")
            out.append("")

    return "\n".join(out) + "\n"
