import re
import pdfplumber
import io
import pandas as pd

def process_pdf_bytes_debug(pdf_bytes):
    """
    Processa os bytes do PDF e retorna um DataFrame e uma lista de debug.
    Corrige problemas de nomes quebrados em múltiplas linhas.
    """
    debug_records = []
    items = []
    
    # Regex para identificar o início de um item: "N. 123 -"
    # Captura: (Grupo 1: Número) (Grupo 2: Resto do texto)
    line_start_pattern = re.compile(r"^\s*N\.\s*(\d+)\s*-\s*(.*)$", re.IGNORECASE)
    
    # Regex para ignorar cabeçalhos/rodapés comuns (ajuste conforme seu PDF)
    ignore_pattern = re.compile(r"(page \d+|compras\.gov\.br|total linhas)", re.IGNORECASE)

    current_item = None

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page_num, page in enumerate(pdf.pages):
            # layout=True é essencial para manter a 'visualidade' do texto
            text = page.extract_text(layout=True)
            if not text:
                continue
                
            lines = text.splitlines()
            
            for i, line in enumerate(lines):
                line_clean = line.strip()
                
                if not line_clean: 
                    continue
                    
                # Se for cabeçalho ou rodapé irrelevante, pula
                if ignore_pattern.search(line_clean) and len(line_clean) < 40:
                    continue

                # Tenta casar com o padrão "N. XX -"
                match = line_start_pattern.match(line_clean)

                if match:
                    # --- NOVO REGISTRO ENCONTRADO ---
                    
                    # 1. Se já existe um item sendo construído, salvamos ele agora
                    if current_item:
                        items.append(current_item)
                        # Adiciona ao debug
                        debug_records.append(f"SAVED | {current_item['numero']} | {current_item['nome']}")

                    # 2. Inicia o novo item
                    numero = match.group(1)
                    texto_inicial = match.group(2)
                    
                    current_item = {
                        "numero": numero,
                        "nome": texto_inicial, # Começa com o texto da primeira linha
                        "raw_lines": [line_clean] # Para debug
                    }
                else:
                    # --- LINHA DE CONTINUAÇÃO ---
                    
                    # Se temos um item aberto, essa linha provavelmente pertence a ele
                    if current_item:
                        # Heurística: Às vezes o PDF tem lixo entre linhas.
                        # Só concatene se parecer parte do nome (ex: não é um número solto de página)
                        
                        # Adiciona espaço e concatena
                        current_item["nome"] += " " + line_clean
                        current_item["raw_lines"].append(line_clean)
                    else:
                        # Texto solto sem item prévio (provavelmente cabeçalho inicial do documento)
                        debug_records.append(f"IGNORED | {line_clean}")

    # Não esquecer de salvar o último item do loop
    if current_item:
        items.append(current_item)
        debug_records.append(f"SAVED | {current_item['numero']} | {current_item['nome']}")

    # Cria o DataFrame
    df = pd.DataFrame(items)
    
    # Limpeza final (opcional, mas recomendada para remover excesso de espaços)
    if not df.empty and 'nome' in df.columns:
        df['nome'] = df['nome'].str.replace(r'\s+', ' ', regex=True).str.strip()

    return df, debug_records

def validate_extraction(df):
    """
    Função auxiliar que você já usava, adaptada para o novo DF.
    """
    if df.empty:
        return {"total_rows": 0, "rows_nome_vazio": 0, "pct_nome_vazio": 0}
        
    total = len(df)
    vazios = df[df['nome'] == ''].shape[0]
    return {
        "total_rows": total,
        "rows_nome_vazio": vazios,
        "pct_nome_vazio": round((vazios / total) * 100, 2)
    }

def debug_dump(df, debug_records, max_rows=100):
    """
    Formata o dump para visualização no response.
    """
    out = []
    out.append("--- LOG DE CONSTRUÇÃO (Primeiros 50 eventos) ---")
    out.extend(debug_records[:50])
    out.append("")
    out.append("--- DATAFRAME FINAL (Amostra) ---")
    
    if df.empty:
        out.append("DataFrame Vazio.")
    else:
        # Formata bonitinho como tabela de texto
        txt_table = df.head(max_rows).to_string(index=False)
        out.append(txt_table)
        
    return "\n".join(out)
