"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";

// ... (restante dos imports originais do seu arquivo)

// -------------------------------------------------------------------------------------
// Tipos
// -------------------------------------------------------------------------------------
type PreviewItem = {
  catmat: string; // vem do parser como string
  descricao: string;
  unidade: string;
  quantidade: number;
  valor_unitario: number | null;
  valor_total: number | null;
};

type PncpPriceRow = {
  catmat: number;
  data_resultado_iso: string | null;
  data_resultado_br: string | null;
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
  compra_link: string | null;
};

// ... (restante do seu arquivo original)

// -------------------------------------------------------------------------------------
// Página
// -------------------------------------------------------------------------------------
export default function PrecosPage() {
  // ... (estados originais)

  const [preview, setPreview] = useState<PreviewItem[]>([]);
  const [previewReady, setPreviewReady] = useState(false);

  // PNCP: cache de “últimos preços” (por CATMAT)
  const [pncpLatestByCatmat, setPncpLatestByCatmat] = useState<Record<number, PncpPriceRow | null>>({});
  const [pncpHistoryByCatmat, setPncpHistoryByCatmat] = useState<Record<number, PncpPriceRow[]>>({});
  const [pncpLoading, setPncpLoading] = useState(false);
  const [pncpError, setPncpError] = useState<string | null>(null);

  // Modal histórico
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyCatmat, setHistoryCatmat] = useState<number | null>(null);

  // ... (restante dos hooks/refs originais)

  // -----------------------------------------------------------------------------------
  // Auto-preenche "Último licitado/estimado" (e metadados) via PNCP com base no CATMAT
  // -----------------------------------------------------------------------------------
  useEffect(() => {
    // A prévia do parser traz CATMAT como string. Para buscar PNCP, normalizamos para number.
    const catmats = Array.from(
      new Set(
        preview
          .map((p) => Number(p.catmat))
          .filter((n): n is number => Number.isFinite(n) && n > 0)
      )
    );

    if (!previewReady || catmats.length === 0) return;

    let cancelled = false;

    async function fetchPncpLatest() {
      setPncpLoading(true);
      setPncpError(null);

      try {
        const res = await fetch("/api/pncp/latest", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ catmats }),
        });

        if (!res.ok) {
          const t = await res.text();
          throw new Error(t || `HTTP ${res.status}`);
        }

        const data = (await res.json()) as {
          latestByCatmat: Record<string, PncpPriceRow | null>;
        };

        if (cancelled) return;

        const normalized: Record<number, PncpPriceRow | null> = {};
        for (const [k, v] of Object.entries(data.latestByCatmat || {})) {
          const keyNum = Number(k);
          if (Number.isFinite(keyNum)) normalized[keyNum] = v;
        }

        setPncpLatestByCatmat((prev) => ({ ...prev, ...normalized }));

        // Aqui você pode encaixar o auto-preenchimento no seu state de itens do app,
        // respeitando “não sobrescrever se usuário já digitou”.
        // (mantive sua lógica original — apenas corrigindo o CATMAT fonte.)
      } catch (e: any) {
        if (!cancelled) setPncpError(e?.message || "Erro ao consultar PNCP");
      } finally {
        if (!cancelled) setPncpLoading(false);
      }
    }

    fetchPncpLatest();

    return () => {
      cancelled = true;
    };
  }, [previewReady, preview]);

  // -----------------------------------------------------------------------------------
  // Abre modal de histórico PNCP
  // -----------------------------------------------------------------------------------
  async function openHistoryModal(catmat: number) {
    setHistoryCatmat(catmat);
    setHistoryOpen(true);

    // se já tem cache, não refaz
    if (pncpHistoryByCatmat[catmat]?.length) return;

    try {
      setPncpLoading(true);
      setPncpError(null);

      const res = await fetch("/api/pncp/history", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ catmat }),
      });

      if (!res.ok) {
        const t = await res.text();
        throw new Error(t || `HTTP ${res.status}`);
      }

      const data = (await res.json()) as { rows: PncpPriceRow[] };

      setPncpHistoryByCatmat((prev) => ({ ...prev, [catmat]: data.rows || [] }));
    } catch (e: any) {
      setPncpError(e?.message || "Erro ao carregar histórico PNCP");
    } finally {
      setPncpLoading(false);
    }
  }

  // -----------------------------------------------------------------------------------
  // Render
  // -----------------------------------------------------------------------------------
  return (
    <div>
      {/* ... (header/layout original) */}

      {/* PRÉVIA */}
      <section>
        <h2>Prévia</h2>
        <div style={{ fontSize: 13, opacity: 0.85 }}>Preços PNCP carregados (quando houver).</div>

        {/* ... (restante da sua UI original da prévia) */}
      </section>

      {/* ... (restante do seu arquivo original) */}

      {/* Exemplo de onde antes usava r.codigo_item (corrigido) */}
      {/* (Este trecho está no mesmo lugar do seu arquivo, só corrigi o CATMAT) */}
      {/* ... */}

      {/* Modal histórico */}
      {historyOpen && historyCatmat != null && (
        <div>
          {/* ... (UI do modal original) */}
          <div style={{ fontWeight: 700, marginBottom: 8 }}>Histórico PNCP — CATMAT {historyCatmat}</div>

          {pncpError && <div style={{ color: "red" }}>{pncpError}</div>}

          {/* tabela */}
          <div style={{ overflowX: "auto" }}>
            <table>
              <thead>
                <tr>
                  <th>Data</th>
                  <th>Pregão</th>
                  <th>Item</th>
                  <th>Estimado</th>
                  <th>Licitado</th>
                  <th>Fornecedor</th>
                  <th>Status</th>
                  <th>Link</th>
                </tr>
              </thead>
              <tbody>
                {(pncpHistoryByCatmat[historyCatmat] || []).map((row, idx) => (
                  <tr key={idx}>
                    <td>{row.data_resultado_br || "-"}</td>
                    <td>{row.pregao || "-"}</td>
                    <td>{row.numero_item_pncp ?? "-"}</td>
                    <td>{row.valor_estimado_br || "-"}</td>
                    <td>{row.valor_licitado_br || "Fracassado"}</td>
                    <td>{row.nome_fornecedor || "-"}</td>
                    <td>{row.situacao_compra_item_nome || "-"}</td>
                    <td>
                      {row.compra_link ? (
                        <a href={row.compra_link} target="_blank" rel="noreferrer">
                          Abrir
                        </a>
                      ) : (
                        "-"
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <button onClick={() => setHistoryOpen(false)}>Fechar</button>
        </div>
      )}
    </div>
  );
}
