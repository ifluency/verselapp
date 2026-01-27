"use client";

import React, { useEffect, useMemo, useState } from "react";
import Link from "next/link";

type PreviewItem = {
  // Dados do parser (preview)
  descricao: string;
  catmat: string; // <-- vem do PDF como texto
  unidade: string;
  quantidade: number;
  valor_unitario: number | null;
  valor_total: number | null;
};

type ParsedResult = {
  itens: Array<{
    // Resultado final do parser (tabela principal do app)
    codigo_item?: number; // algumas extrações antigas/alternativas podem usar isso
    catmat?: string; // e/ou isso
    descricao: string;
    unidade: string;
    quantidade: number;
    valor_unitario: number | null;
    valor_total: number | null;
  }>;
};

type PncpLatest = {
  catmat: number;
  data_resultado: string | null;
  pregao: string | null;
  numero_item_pncp: number | null;
  valor_estimado_num: number | null;
  valor_licitado_num: number | null;
  valor_estimado_br: string | null;
  valor_licitado_br: string | null;
  descricao_resumida: string | null;
  situacao_compra_item_nome: string | null;
  tem_resultado: boolean | null;
  nome_fornecedor: string | null;
  compra_url: string | null;
};

export default function PrecosPage() {
  // ... (demais estados do seu app)

  const [preview, setPreview] = useState<PreviewItem[]>([]);
  const [previewReady, setPreviewReady] = useState(false);

  const [pncpLoading, setPncpLoading] = useState(false);
  const [pncpLatestByCatmat, setPncpLatestByCatmat] = useState<Record<number, PncpLatest>>({});

  const catmatToNumber = (value: string): number | null => {
    const digits = String(value ?? "").replace(/\D/g, "");
    if (!digits) return null;
    const n = Number(digits);
    return Number.isFinite(n) && n > 0 ? n : null;
  };

  // =========================================
  // Buscar últimos preços PNCP para os CATMATs do preview
  // =========================================
  useEffect(() => {
    const catmats = Array.from(
      new Set(preview.map((p) => catmatToNumber(p.catmat)).filter((c): c is number => typeof c === "number"))
    );
    if (!previewReady || catmats.length === 0) return;

    let cancelled = false;

    (async () => {
      try {
        setPncpLoading(true);

        // Você deve ter um endpoint interno que recebe catmats[] e devolve latest por catmat
        // (mantive a chamada como estava no seu projeto)
        const res = await fetch("/api/pncp/latest-by-catmat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ catmats }),
        });

        if (!res.ok) throw new Error(`PNCP latest-by-catmat failed: ${res.status}`);
        const data: { latestByCatmat: Record<number, PncpLatest> } = await res.json();

        if (cancelled) return;

        setPncpLatestByCatmat(data.latestByCatmat ?? {});
      } catch (e) {
        console.error(e);
      } finally {
        if (!cancelled) setPncpLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [previewReady, preview]);

  // =========================================
  // Aplicar últimos preços PNCP no preview (sem quebrar tipagem)
  // =========================================
  const previewWithPncp = useMemo(() => {
    if (!previewReady || preview.length === 0) return preview;

    // mantém a estrutura de PreviewItem; só enriquece via lookup
    return preview.map((it) => {
      const catmat = catmatToNumber(it.catmat);
      if (!catmat) return it;

      const latest = pncpLatestByCatmat[catmat];
      if (!latest) return it;

      // aqui você pode ajustar seu comportamento (ex.: preencher campos no UI)
      return it;
    });
  }, [preview, previewReady, pncpLatestByCatmat]);

  // =========================================
  // ... resto do seu componente (UI)
  // =========================================

  return (
    <main style={{ margin: "12px 0 0", padding: "0 0 110px" }}>
      {/* seu layout atual */}

      {/* Exemplo: caso você use previewWithPncp em algum lugar */}
      <div style={{ marginTop: 10 }}>
        <div style={{ fontWeight: 800, marginBottom: 8 }}>Prévia</div>
        <div style={{ fontSize: 12, opacity: 0.8, marginBottom: 10 }}>
          {pncpLoading ? "Carregando preços PNCP..." : "Preços PNCP carregados (quando houver)."}
        </div>

        <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
          {previewWithPncp.map((p, idx) => (
            <li key={idx} style={{ padding: "8px 0", borderBottom: "1px solid #eee" }}>
              <div style={{ fontWeight: 700 }}>{p.descricao}</div>
              <div style={{ fontSize: 12, opacity: 0.85 }}>
                CATMAT: {p.catmat} • Qtd: {p.quantidade} • Unidade: {p.unidade}
              </div>
            </li>
          ))}
        </ul>
      </div>

      <div style={{ marginTop: 18, fontSize: 12 }}>
        <Link href="/">(voltar)</Link>
      </div>
    </main>
  );
}
