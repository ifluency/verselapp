"use client";

import React from "react";
import Link from "next/link";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section
      style={{
        border: "1px solid #e5e7eb",
        borderRadius: 14,
        background: "#ffffff",
        padding: 16,
      }}
    >
      <div style={{ fontSize: 14, fontWeight: 900, color: "#111827" }}>{title}</div>
      <div style={{ marginTop: 8, color: "#374151", fontSize: 13, lineHeight: 1.55 }}>{children}</div>
    </section>
  );
}

function Bullet({ children }: { children: React.ReactNode }) {
  return (
    <li style={{ margin: "6px 0" }}>
      <span style={{ color: "#111827" }}>{children}</span>
    </li>
  );
}

export default function ManualPage() {
  return (
    <main style={{ margin: "12px 0 0", padding: "0 0 110px" }}>
      <div style={{ marginTop: 4, marginBottom: 10 }}>
        <div style={{ fontSize: 18, fontWeight: 900, color: "#111827" }}>Manual de Utilização</div>
        <div style={{ marginTop: 4, fontSize: 13, color: "#4b5563" }}>
          Orientações para navegar e utilizar as ferramentas disponíveis no Painel UPDE.
        </div>
      </div>

      <div style={{ display: "grid", gap: 12 }}>
        <Section title="1) Acesso e navegação">
          <ul style={{ margin: "8px 0 0", paddingLeft: 18 }}>
            <Bullet>
              A página inicial (
              <code style={{ fontFamily: "inherit" }}>/</code>) é o login. Após autenticar, você será direcionado para a
              ferramenta de <strong>Formação de Preços de Referência</strong>.
            </Bullet>
            <Bullet>
              Nas páginas internas, use o ícone no canto superior esquerdo para abrir o menu e alternar entre as
              ferramentas.
            </Bullet>
            <Bullet>
              Para encerrar a sessão, utilize o botão <strong>Sair</strong> no topo.
            </Bullet>
          </ul>
        </Section>

        <Section title="2) Formação de Preços de Referência">
          <div style={{ marginTop: 6 }}>
            A ferramenta está disponível em <Link href="/precos">/precos</Link>.
          </div>
          <ul style={{ margin: "8px 0 0", paddingLeft: 18 }}>
            <Bullet>
              <strong>1. Upload:</strong> selecione o arquivo de entrada conforme o padrão do setor.
            </Bullet>
            <Bullet>
              <strong>2. Prévia:</strong> confira os itens e os valores extraídos.
            </Bullet>
            <Bullet>
              <strong>3. Último licitado:</strong> informe/ajuste os valores quando aplicável.
            </Bullet>
            <Bullet>
              <strong>4. Ajuste manual:</strong> quando liberado pelo sistema, refine método/itens considerados.
            </Bullet>
            <Bullet>
              <strong>5. Gerar ZIP:</strong> ao final, o sistema gera um ZIP com os PDFs.
            </Bullet>
          </ul>
        </Section>

        <Section title="3) Consulta CATMAT">
          <div style={{ marginTop: 6 }}>
            A ferramenta está disponível em <Link href="/catmat">/catmat</Link> e consulta o status do item no cadastro de
            materiais.
          </div>
          <ul style={{ margin: "8px 0 0", paddingLeft: 18 }}>
            <Bullet>
              Cole a lista de CATMATs (1 por linha). A aplicação ignora cabeçalhos e linhas em branco.
            </Bullet>
            <Bullet>
              Clique em <strong>Consultar</strong> para buscar os registros.
            </Bullet>
            <Bullet>
              Os resultados são separados em duas tabelas: <strong>Ativos</strong> e <strong>Inativos</strong>.
            </Bullet>
            <Bullet>
              Use o botão <strong>Copiar inativos</strong> para copiar apenas os códigos inativos (1 por linha) e compartilhar.
            </Bullet>
            <Bullet>
              Se algum CATMAT falhar na consulta, o sistema segue com os próximos e exibe no final quantos tiveram erro.
            </Bullet>
          </ul>
        </Section>

        <Section title="4) Dicas rápidas">
          <ul style={{ margin: "8px 0 0", paddingLeft: 18 }}>
            <Bullet>Se a lista for grande, aguarde a conclusão (o status no topo informa o andamento).</Bullet>
            <Bullet>Se houver instabilidade da API externa, alguns CATMATs podem aparecer como “sem retorno/erro”.</Bullet>
          </ul>
        </Section>
      </div>
    </main>
  );
}
