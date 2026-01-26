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

export default function ManualPrecosPage() {
  return (
    <main style={{ margin: "12px 0 0", padding: "0 0 110px" }}>
      <div style={{ marginTop: 4, marginBottom: 10 }}>
        <div style={{ fontSize: 18, fontWeight: 900, color: "#111827" }}>Manual — Preços de Referência</div>
        <div style={{ marginTop: 4, fontSize: 13, color: "#4b5563", lineHeight: 1.5 }}>
          Passo a passo para gerar os relatórios (prévia → ajustes → ZIP final).
        </div>
        <div style={{ marginTop: 8, display: "flex", gap: 10, flexWrap: "wrap" }}>
          <Link className="btn btnGhost" href="/manual" style={{ height: 36, display: "inline-flex", alignItems: "center" }}>
            ← Voltar para o Manual
          </Link>
          <Link className="btn" href="/precos" style={{ height: 36, display: "inline-flex", alignItems: "center" }}>
            Ir para a ferramenta
          </Link>
        </div>
      </div>

      <div style={{ display: "grid", gap: 12 }}>
        <Section title="Visão geral">
          <ul style={{ margin: "8px 0 0", paddingLeft: 18 }}>
            <Bullet>
              A ferramenta está disponível em <Link href="/precos">/precos</Link>.
            </Bullet>
            <Bullet>
              O fluxo padrão é: <strong>Upload</strong> → <strong>Prévia</strong> → <strong>Ajustes</strong> → <strong>Gerar ZIP</strong>.
            </Bullet>
            <Bullet>
              Sempre revise a prévia antes de finalizar, especialmente itens com valores faltantes ou divergentes.
            </Bullet>
          </ul>
        </Section>

        <Section title="Passo a passo">
          <Step n={1} title="Acesse a ferramenta">
            No menu lateral (ícone no canto superior esquerdo), clique em <strong>Preços de Referência</strong> ou acesse diretamente <Link href="/precos">/precos</Link>.
          </Step>
          <Step n={2} title="Faça o upload do arquivo">
            Use o botão de upload para selecionar o arquivo de entrada. Aguarde o carregamento e a validação.
            <div style={{ marginTop: 6, fontSize: 12, color: "#6b7280" }}>
              Dica: se o arquivo não carregar, confirme se você está usando o padrão correto do setor (nome/estrutura de planilha).
            </div>
          </Step>
          <Step n={3} title="Revise a Prévia">
            Após o upload, a aplicação exibe a <strong>Prévia</strong> com os itens e valores extraídos. Revise:
            <ul style={{ margin: "8px 0 0", paddingLeft: 18 }}>
              <Bullet>Se todos os itens esperados apareceram.</Bullet>
              <Bullet>Se há linhas com valores ausentes, muito baixos/altos, ou inconsistentes.</Bullet>
              <Bullet>Se o descritivo/identificação do item corresponde ao que foi solicitado.</Bullet>
            </ul>
          </Step>
          <Step n={4} title="Informe ou ajuste o “Último licitado” (quando aplicável)">
            Quando a etapa solicitar, informe/ajuste os valores referentes ao último licitado. Essa informação impacta o comparativo e/ou os cálculos finais.
          </Step>
          <Step n={5} title="Ajuste manual (quando liberado pelo sistema)">
            Em alguns cenários, o sistema libera ajustes manuais (ex.: selecionar/retirar valores considerados, trocar método, etc.). Use essa etapa para refinar o resultado.
            <div style={{ marginTop: 6, fontSize: 12, color: "#6b7280" }}>
              Dica: mantenha a consistência — qualquer ajuste deve ser justificável no processo.
            </div>
          </Step>
          <Step n={6} title="Gere o ZIP final">
            Clique em <strong>Gerar ZIP</strong> e aguarde a conclusão. Ao final, baixe o arquivo ZIP contendo os PDFs.
          </Step>
        </Section>

        <Section title="Erros comuns e como agir">
          <ul style={{ margin: "8px 0 0", paddingLeft: 18 }}>
            <Bullet>
              <strong>Arquivo inválido:</strong> revise se a planilha segue o padrão exigido (colunas/aba).
            </Bullet>
            <Bullet>
              <strong>Prévia incompleta:</strong> confirme se os itens constam na fonte e se o arquivo não está filtrado/mesclado.
            </Bullet>
            <Bullet>
              <strong>Demora na geração:</strong> arquivos grandes podem levar mais tempo. Evite sair da página durante o processamento.
            </Bullet>
          </ul>
        </Section>

        <Section title="Como adicionar imagens a este manual">
          <div>
            Sim — é possível incluir imagens (prints das telas) neste manual.
            <ol style={{ margin: "8px 0 0", paddingLeft: 18 }}>
              <li style={{ margin: "6px 0" }}>
                Coloque as imagens em <code style={{ fontFamily: "inherit" }}>public/manual/precos/</code> (ex.: <code style={{ fontFamily: "inherit" }}>public/manual/precos/passo-1.png</code>).
              </li>
              <li style={{ margin: "6px 0" }}>
                No arquivo deste manual (<code style={{ fontFamily: "inherit" }}>app/(auth)/manual/precos/page.tsx</code>), adicione um <code style={{ fontFamily: "inherit" }}>&lt;img /&gt;</code> apontando para <code style={{ fontFamily: "inherit" }}>/manual/precos/passo-1.png</code>.
              </li>
            </ol>

            <div style={{ marginTop: 10, fontSize: 12, color: "#6b7280" }}>
              Observação: para evitar “imagem quebrada”, só inclua a tag quando o arquivo existir.
            </div>
          </div>
        </Section>
      </div>
    </main>
  );
}
