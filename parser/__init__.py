import io
import re
import pdfplumber
import pandas as pd

def process_pdf_bytes_debug(pdf_bytes):
    """
    Processa o PDF detectando quebras de linha nos nomes dos fornecedores.
    Retorna: (DataFrame, lista_de_debug)
    """
    debug_records = []
    data = []

    # Regex para capturar "N. 123 - Nome do Fornecedor..."
    # Grupo 1: Número
    # Grupo 2: Texto inicial
    re_inicio_item = re.compile(r"^\s*N\.\s*(\d+)\s*-\s*(.*)$", re.IGNORECASE)
    
    # Regex para capturar o agrupador "Item X" (opcional, para contexto)
    re_item_pai = re.compile(r"^\s*Item\s+(\d+)", re.IGNORECASE)

    # Estado atual da leitura
    current_grupo = ""
    current_record = None

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            # layout=True é essencial para leitura visual linha a linha
            text = page.extract_text(layout=True)
            if not text:
                continue

            lines = text.splitlines()

            for line in lines:
                line_clean = line.strip()
                if not line_clean:
                    continue

                # A) Detecta cabeçalho de Grupo (ex: "Item 2")
                match_pai = re_item_pai.match(line_clean)
                if match_pai:
                    current_grupo = match_pai.group(1)
                    continue

                # B) Detecta Novo Fornecedor (ex: "N. 51 - Wel Distribuidora...")
                match_fornecedor = re_inicio_item.match(line_clean)
                if match_fornecedor:
                    # 1. Se já existia um registro aberto, salva ele agora
                    if current_record:
                        data.append(current_record)

                    # 2. Inicia o novo registro
                    numero = match_fornecedor.group(1)
                    nome_parcial = match_fornecedor.group(2).strip()

                    current_record = {
                        "Item": current_grupo,
                        "Numero": numero,
                        "Fornecedor": nome_parcial # Começa com o texto desta linha
                    }
                
                # C) Detecta Continuação de Nome (Multilinha)
                elif current_record:
                    # Filtra lixo comum de rodapé/cabeçalho
                    if "Compras.gov.br" in line_clean or "Página" in line_clean or "Total linhas" in line_clean:
                        continue
                    
                    # Se a linha não começa com "N.", assumimos que é parte do nome anterior
                    current_record["Fornecedor"] += " " + line_clean

    # Salva o último registro pendente após o loop
    if current_record:
        data.append(current_record)

    # Cria o DataFrame
    df = pd.DataFrame(data)

    if not df.empty:
        # Limpeza final: remove múltiplos espaços criados na concatenação
        df["Fornecedor"] = df["Fornecedor"].str.replace(r'\s+', ' ', regex=True).str.strip()
    else:
        # Garante colunas mesmo vazio para não quebrar o Excel
        df = pd.DataFrame(columns=["Item", "Numero", "Fornecedor"])

    return df, debug_records

def validate_extraction(df):
    """Retorna estatísticas simples sobre a extração."""
    if df.empty:
        return {"total_rows": 0, "rows_nome_vazio": 0, "pct_nome_vazio": 0}
    
    total = len(df)
    vazios = df[df["Fornecedor"] == ""].shape[0]
    
    return {
        "total_rows": total,
        "rows_nome_vazio": vazios,
        "pct_nome_vazio": round((vazios / total) * 100, 2)
    }

def debug_dump(df, debug_records, max_rows=100):
    """Apenas para compatibilidade, caso precise visualizar texto."""
    return df.to_string()
