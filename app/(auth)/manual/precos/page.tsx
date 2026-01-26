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
            Use o botão de "Escolher arquivo" para selecionar o arquivo de entrada, e depois clique em "Gerar Prévia". Aguarde o carregamento e a validação.
            <ul style={{ margin: "8px 0 0", paddingLeft: 18 }}>
              <Bullet>O PDF utilizado deverá ser a pesquisa de preços realizada no ComprasGOV.</Bullet>
              <Bullet>Sempre utilizar o "Relatório Resumido".</Bullet>              
              <Bullet>As inforamações serão retiradas unicamente desse arquivo. Qualquer alteração posterior deve ser refeita pelo ComprasGOV e realizado upload novamente aqui.</Bullet>
            </ul>
            </div>
          </Step>

              <Step n={3} title="Complete as informações da lista">
            Complete as informações necessárias a respeito da lista para prosseguir: número da lista, nome da lista, Processo SEI e Responsável.
            <ul style={{ margin: "8px 0 0", paddingLeft: 18 }}>
                    </Step>

          <Step n={3} title="Informe ou ajuste o “Último licitado” (quando aplicável)">
            Quando a etapa solicitar, informe/ajuste os valores referentes ao último licitado. Essa informação impacta o comparativo e/ou os cálculos finais.
            <ul style={{ margin: "8px 0 0", paddingLeft: 18 }}>
              <Bullet>O campo "Último Licitado" deve ser preenchido com o último valor cotado para o item. A partir desse valor, será calculado a diferente entre o Valor Calculado e último licitado (Coluna Dif. (R$)).</Bullet>
              <Bullet>Se o Valor Calculado for menor do que o Último licitado, ou até 20% maior, o campo de Ajuste será liberado, permitindo realizar de forma manual a seleção dos valores considerados, e o modo de cálculo (Coluna Modo) será atualizado de "Automático" para "Manual".</Bullet>
            </ul>
          </Step>
        
          <Step n={4} title="Revise a Prévia">
            Após o upload, a aplicação exibe a <strong>Prévia</strong> com os itens e valores extraídos. Todos os preços encontrados que foram considerados no ComprasGOV (Compõe = Sim) aparecerão no relatório prévio.
            <ul style={{ margin: "8px 0 0", paddingLeft: 18 }}>
              <Bullet>A coluna "Entradas Iniciais" demonstra quantos preços foram extraídos do PDF, ou seja, todos aqueles que foram considerados na composição do ComprasGOV.</Bullet>
              <Bullet>A coluna "Entradas Finais" demonstra quantos preços restaram após a exclusão dos execessivamente elevados (Coluna Excl. Altos) e dos inexequíveis (Coluna Excl. Inexequíveis).</Bullet>
              <Bullet>A coluna "Valor Calculado" é a média dos valores após as exclusões, quando há 5 ou mais preços de entrada; e a média ou mediana dos valores quando há menos de 5 entradas.</Bullet>
                         </ul>
          </Step>
          
        
          <Step n={5} title="Ajuste manual (quando liberado pelo sistema)">
            Quando o Valor Calculado pelo sistema for menor ou até 20% maior do que o último licitado, poderá ser realizado o ajuste manual. Permite selecionar e/ou retirar valores considerados/desconsiderados, e trocar o método de cálculo. Lembre-se: Toda alteração manual faz-se necessário JUSTIFICATIVA.
            <ul style={{ margin: "8px 0 0", paddingLeft: 18 }}>
              <Bullet>Os valores pré-selecionados são aqueles que foram utilizados no cálculo automático.</Bullet>
              <Bullet>Valores com o fundo em vermelho foram aqueles considerados excessivamente elevados e foram excluídos do cálculo automático.</Bullet>
              <Bullet>Valores com o fundo em amarelo foram aqueles considerados inexequíveis e foram excluídos do cálculo automático.</Bullet>
              <Bullet>Os cálculos de quantidade incluída, quantidade excluída, média, mediana, coeficiente de variação e valor estimado são dinâmicos, ou seja, ao selecionar/deselecionar um valor ao lado, eles mudarão pois serão recalculados.</Bullet>
              <Bullet>Permite e faz-se necessário JUSTIFICAR toda e qualquer alteração manual.</Bullet>
            </ul>
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

       
      </div>
    </main>
  );
}
