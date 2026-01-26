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

function Step({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <div style={{ borderLeft: "3px solid #111827", paddingLeft: 12, margin: "10px 0" }}>
      <div style={{ fontWeight: 900, color: "#111827" }}>
        {n}. {title}
      </div>
      <div style={{ marginTop: 6 }}>{children}</div>
    </div>
  );
}

function Bullet({ children }: { children: React.ReactNode }) {
  return (
    <li style={{ margin: "6px 0" }}>
      <span style={{ color: "#111827" }}>{children}</span>
    </li>
  );
}

export default function ManualCatmatPage() {
  return (
    <main style={{ margin: "12px 0 0", padding: "0 0 110px" }}>
      <div style={{ marginTop: 4, marginBottom: 10 }}>
        <div style={{ fontSize: 18, fontWeight: 900, color: "#111827" }}>Manual — Consulta CATMAT</div>
        <div style={{ marginTop: 4, fontSize: 13, color: "#4b5563", lineHeight: 1.5 }}>
          Passo a passo para consultar CATMATs.
        </div>
        <div style={{ marginTop: 8, display: "flex", gap: 10, flexWrap: "wrap" }}>
          <Link className="btn btnGhost" href="/manual" style={{ height: 36, display: "inline-flex", alignItems: "center" }}>
            ← Voltar para o Manual
          </Link>
          <Link className="btn" href="/catmat" style={{ height: 36, display: "inline-flex", alignItems: "center" }}>
            Ir para a ferramenta
          </Link>
        </div>
      </div>

      <div style={{ display: "grid", gap: 12 }}>
        <Section title="Visão geral">
          <ul style={{ margin: "8px 0 0", paddingLeft: 18 }}>
            <Bullet>
              A ferramenta está disponível em <Link href="/catmat">/catmat</Link>.
            </Bullet>
            <Bullet>
              Você cola uma lista de códigos (1 por linha), clica em <strong>Consultar</strong>, e o sistema separa em <strong>Ativos</strong> e <strong>Inativos</strong>.
            </Bullet>
            <Bullet>
              No final, a tela mostra um resumo: <strong>Pesquisados</strong>, <strong>Ativos</strong>, <strong>Inativos</strong> e <strong>CATMATs com erro</strong> (quando houver).
            </Bullet>
          </ul>
        </Section>

        <Section title="Passo a passo">
          <Step n={1} title="Copie os CATMATs">
            A lista deve conter <strong>1 CATMAT por linha</strong>. Pode copiar diretamente a coluna do Access ou de um arquivo Excel. A aplicação:
            <ul style={{ margin: "8px 0 0", paddingLeft: 18 }}>
              <Bullet>Ignora cabeçalhos (ex.: “CATMAT”, “Código”, etc.).</Bullet>
              <Bullet>Ignora linhas em branco.</Bullet>
              <Bullet>Ignora espaços e repetições.</Bullet>
            </ul>
          </Step>

          <Step n={2} title="Cole os códigos no campo de texto">
            Cole tudo no campo <strong>“Cole a lista de CATMATs aqui, 1 por linha”</strong>.
            <div style={{ marginTop: 6, fontSize: 12, color: "#6b7280" }}>
              Dica: se você colar com colunas do Excel, confira se veio apenas uma coluna com os códigos.
            </div>
          </Step>

          <Step n={3} title="Clique em Consultar e aguarde">
            A ferramenta consulta a base pública do ComprasGOV, e vai atualizando o status. Em listas grandes, o tempo pode variar conforme a estabilidade da API.
          </Step>

          <Step n={4} title="Interprete os resultados (Ativos x Inativos)">
            Ao concluir, você verá duas tabelas:
            <ul style={{ margin: "8px 0 0", paddingLeft: 18 }}>
              <Bullet>
                <strong>Ativos:</strong> itens que estão ativos no cadastro.
              </Bullet>
              <Bullet>
                <strong>Inativos:</strong> itens inativos no cadastro (normalmente exigem correção/substituição no TR).
              </Bullet>
              <Bullet>
                O <strong>Descritivo</strong> aparece truncado em até 150 caracteres para facilitar leitura.
              </Bullet>
            </ul>
          </Step>

          <Step n={5} title="Copie os inativos">
            Clique em <strong>“Copiar CATMATs inativos”</strong>. Isso copia apenas os códigos inativos (1 por linha).
          </Step>

          <Step n={6} title="Entenda “CATMATs com erro”">
            Quando houver falha na consulta (ex.: instabilidade da API, timeout, código inválido), o sistema segue com os demais itens e contabiliza no final.
            <div style={{ marginTop: 6, fontSize: 12, color: "#6b7280" }}>
              Sugestão: se aparecerem muitos erros, repita a consulta em alguns minutos e/ou divida a lista.
            </div>
          </Step>
        </Section>

       
      </div>
    </main>
  );
}
