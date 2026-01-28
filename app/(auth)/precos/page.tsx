"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";

/**
 * Observação:
 * - Este arquivo é grande porque concentra o fluxo completo: upload → prévia → último licitado → ajuste manual → geração de PDFs ZIP.
 * - Ajustes feitos nesta revisão:
 *   1) Vazamento de texto na coluna “Último licitado” (ellipsis/nowrap em todas as linhas).
 *   2) Histórico PNCP: corrigido mapeamento para o formato retornado pelo endpoint /api/catmat_historico.
 *   3) Barra fixa/containers: largura e padding alinhados às novas margens globais (≈1/3).
 */

type PreviewItem = {
  item: string;
  catmat: number | null;
  n_bruto: number;
  n_final: number;
  excl_altos: number;
  excl_baixos: number;
  valor_calculado: number | null;
  modo: "Auto" | "Manual" | string;
  metodo: string;
  valor_final: number | null;
  rep?: any;
};

type OverrideData = {
  modo: "Manual";
  selecionados: number[]; // índices dos valores brutos selecionados
  justificativa: string; // texto final
  justificativa_code?: string;
};

type ManualEntry = {
  idx: number;
  valor: number;
  fonte?: string;
};

type ManualModalState = {
  open: boolean;
  itemId: string;
  entries: ManualEntry[];
  selected: Set<number>;
  autoKept: Set<number>;
  autoExclAltos: Set<number>;
  autoExclBaixos: Set<number>;
  justificativa: string;
  justificativaOutro: string;
  justificativaSelected: string;
  previewItem?: PreviewItem;
};

type PncpUltimoInfo =
  | {
      status: "ok";
      catmat: string;
      data_resultado_iso: string | null;
      data_resultado_br: string;
      pregao: string;
      numero_item_pncp: number | null;
      valor_unitario_estimado_num: number | null;
      valor_unitario_resultado_num: number | null;
      nome_fornecedor: string;
      situacao_compra_item_nome: string;
      compra_link: string;
    }
  | {
      status: "fracassado";
      catmat: string;
      data_resultado_iso: string | null;
      data_resultado_br: string;
      pregao: string;
      numero_item_pncp: number | null;
      valor_unitario_estimado_num: number | null;
      valor_unitario_resultado_num: null;
      nome_fornecedor: string;
      situacao_compra_item_nome: string;
      compra_link: string;
    }
  | {
      status: "nao_encontrado";
      catmat: string;
    };

type PncpHistoricoRow = {
  seq: number;
  data_resultado_iso: string | null;
  data_resultado_br: string;
  pregao: string;
  numero_item_pncp: number | null;
  situacao: string;
  fornecedor: string;
  link: string;
  valor_estimado_num: number | null;
  valor_licitado_num: number | null;
};

function parseBRL(input: string): number | null {
  const s = (input || "")
    .trim()
    .replace(/R\$\s?/i, "")
    .replace(/\./g, "")
    .replace(/,/g, ".");
  if (!s) return null;
  const n = Number(s);
  return Number.isFinite(n) ? n : null;
}

function safeSlug(input: string): string {
  const s = (input || "").trim();
  const slug = s
    .replace(/[^0-9A-Za-z._-]+/g, "_")
    .replace(/_+/g, "_")
    .replace(/^_+|_+$/g, "");
  return slug || "SEM_NUMERO";
}

function fmtBRL(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return "";
  return n.toFixed(2).replace(".", ",");
}

function fmtSmart(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return "";
  if (n >= 1) return n.toFixed(2).replace(".", ",");
  return n.toFixed(4).replace(".", ",");
}

function nowPtBR(): string {
  const d = new Date();
  const pad2 = (x: number) => String(x).padStart(2, "0");
  return `${pad2(d.getDate())}/${pad2(d.getMonth() + 1)}/${d.getFullYear()} ${pad2(
    d.getHours()
  )}:${pad2(d.getMinutes())}`;
}

const SHOW_DEBUG = false; // apenas desativar UI; manter funcionalidade no código

export default function PrecosPage() {
  const [file, setFile] = useState<File | null>(null);
  const [status, setStatus] = useState("");
  const [loadingPreview, setLoadingPreview] = useState(false);
  const [loadingGenerate, setLoadingGenerate] = useState(false);

  // Metadados obrigatórios (Lista/SEI/Responsável)
  const [numeroLista, setNumeroLista] = useState("");
  const [nomeLista, setNomeLista] = useState("");
  const [processoSEI, setProcessoSEI] = useState("");
  const [responsavel, setResponsavel] = useState("");

  // Prévia de itens
  const [preview, setPreview] = useState<PreviewItem[]>([]);
  const [previewReady, setPreviewReady] = useState(false);

  // Último licitado manual por item
  const [lastQuotes, setLastQuotes] = useState<Record<string, string>>({});
  const [activeLastQuoteRow, setActiveLastQuoteRow] = useState<string | null>(null);

  // Overrides manuais por item
  const [overrides, setOverrides] = useState<Record<string, OverrideData>>({});

  // PNCP (Neon) - preenchimento automático
  const [pncpUltimoLoading, setPncpUltimoLoading] = useState(false);
  const [pncpUltimoByItem, setPncpUltimoByItem] = useState<Record<string, PncpUltimoInfo>>({});

  // Modal Histórico PNCP
  const [pncpHistOpen, setPncpHistOpen] = useState(false);
  const [pncpHistCatmat, setPncpHistCatmat] = useState("");
  const [pncpHistLoading, setPncpHistLoading] = useState(false);
  const [pncpHistError, setPncpHistError] = useState("");
  const [pncpHistRows, setPncpHistRows] = useState<PncpHistoricoRow[]>([]);

  // Modal Ajuste Manual
  const [manualModal, setManualModal] = useState<ManualModalState>({
    open: false,
    itemId: "",
    entries: [],
    selected: new Set<number>(),
    autoKept: new Set<number>(),
    autoExclAltos: new Set<number>(),
    autoExclBaixos: new Set<number>(),
    justificativa: "",
    justificativaOutro: "",
    justificativaSelected: "",
  });

  const fileInputRef = useRef<HTMLInputElement | null>(null);

  // Botões em foco (UX)
  const [stepUploadDone, setStepUploadDone] = useState(false);
  const [stepPreviewDone, setStepPreviewDone] = useState(false);

  // === Regras de cor do botão Ajustar ===
  // - Vermelho: último licitado > valor final
  // - Amarelo: valor final <= 1.2 * último licitado
  // - Verde: já ajustado (override salvo)
  // - Desabilitado: fora da regra
  function getAdjustButtonState(itemId: string, valorFinal: number | null): {
    enabled: boolean;
    color: "red" | "yellow" | "green" | "disabled";
  } {
    const ov = overrides[itemId];
    if (ov?.modo === "Manual") return { enabled: true, color: "green" };

    const last = parseBRL(lastQuotes[itemId] || "");
    if (!Number.isFinite(last as any) || !Number.isFinite(valorFinal as any) || valorFinal === null) {
      return { enabled: false, color: "disabled" };
    }

    if ((last as number) > (valorFinal as number)) return { enabled: true, color: "red" };

    if ((valorFinal as number) <= 1.2 * (last as number)) return { enabled: true, color: "yellow" };

    return { enabled: false, color: "disabled" };
  }

  function computeDif(itemId: string, valorFinal: number | null): number | null {
    const last = parseBRL(lastQuotes[itemId] || "");
    if (!Number.isFinite(last as any) || valorFinal === null || !Number.isFinite(valorFinal as any)) return null;
    return (valorFinal as number) - (last as number);
  }

  function openManualModal(item: PreviewItem) {
    const rep = item.rep || {};
    const vals: number[] = Array.isArray(rep.vals) ? rep.vals : [];
    const fontes: (string | null)[] = Array.isArray(rep.fontes) ? rep.fontes : [];

    const entries: ManualEntry[] = vals
      .map((v, idx) => ({
        idx,
        valor: Number(v),
        fonte: fontes[idx] ? String(fontes[idx]) : "",
      }))
      .filter((e) => Number.isFinite(e.valor))
      .sort((a, b) => a.valor - b.valor);

    // auto kept/excluded indexes (com base no relatório)
    const autoKept = new Set<number>((rep.mantidos_indices || rep.mantidos_idx || []) as number[]);
    const autoExclAltos = new Set<number>((rep.excluidos_altos_idx || rep.excluidos_altos_indices || []) as number[]);
    const autoExclBaixos = new Set<number>((rep.excluidos_baixos_idx || rep.excluidos_baixos_indices || []) as number[]);

    // Seleciona por padrão os mantidos automáticos (mas lembrando que entries foi reordenado)
    // Precisamos mapear: entries contém idx original => é o índice “real”
    const selected = new Set<number>();
    for (const e of entries) {
      if (autoKept.has(e.idx)) selected.add(e.idx);
    }

    setManualModal({
      open: true,
      itemId: item.item,
      entries,
      selected,
      autoKept,
      autoExclAltos,
      autoExclBaixos,
      justificativa: "",
      justificativaOutro: "",
      justificativaSelected: "",
      previewItem: item,
    });
  }

  function closeModal() {
    setManualModal((prev) => ({ ...prev, open: false }));
  }

  function toggleSelect(idx: number) {
    setManualModal((prev) => {
      const next = new Set(prev.selected);
      if (next.has(idx)) next.delete(idx);
      else next.add(idx);
      return { ...prev, selected: next };
    });
  }

  function computeStatsFromSelected(entries: ManualEntry[], selectedIdx: Set<number>) {
    const arr = entries
      .filter((e) => selectedIdx.has(e.idx))
      .map((e) => e.valor)
      .filter((v) => Number.isFinite(v))
      .sort((a, b) => a - b);

    const n = arr.length;
    if (!n) return { n: 0, mean: null as number | null, median: null as number | null, cv: null as number | null };

    const mean = arr.reduce((a, b) => a + b, 0) / n;
    const median = n % 2 === 1 ? arr[(n - 1) / 2] : (arr[n / 2 - 1] + arr[n / 2]) / 2;

    const variance = arr.reduce((acc, x) => acc + Math.pow(x - mean, 2), 0) / n;
    const std = Math.sqrt(variance);
    const cv = mean !== 0 ? std / mean : null;

    return { n, mean, median, cv };
  }

  function saveManualOverride() {
    const id = manualModal.itemId;
    const entries = manualModal.entries;
    const sel = manualModal.selected;

    const stats = computeStatsFromSelected(entries, sel);
    if (stats.n === 0) {
      setStatus("Selecione ao menos 1 valor para salvar o ajuste manual.");
      return;
    }

    // Justificativa final: se selecionou "Outro", usa texto livre; caso contrário, usa texto do dropdown
    const justificativaFinal =
      manualModal.justificativaSelected === "OUTRO"
        ? manualModal.justificativaOutro.trim()
        : manualModal.justificativaSelected.trim();

    if (!justificativaFinal) {
      setStatus("Selecione uma justificativa ou preencha 'Outro'.");
      return;
    }

    setOverrides((prev) => ({
      ...prev,
      [id]: {
        modo: "Manual",
        selecionados: Array.from(sel.values()).sort((a, b) => a - b),
        justificativa: justificativaFinal,
      },
    }));
    closeModal();
  }

  function clearManualOverride(itemId: string) {
    setOverrides((prev) => {
      const copy = { ...prev };
      delete copy[itemId];
      return copy;
    });
  }

  async function hydratePncpUltimo(items: PreviewItem[]) {
    try {
      const catmats = Array.from(
        new Set(
          (items || [])
            .map((it) => String(it.catmat || "").trim())
            .filter((c) => c && /^\d+$/.test(c))
        )
      );
      if (!catmats.length) return;

      setPncpUltimoLoading(true);
      const res = await fetch("/api/ultimo_licitado", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ catmats }),
      });
      if (!res.ok) return;

      const data = await res.json();
      const byCatmat: Record<string, PncpUltimoInfo> = data?.by_catmat || {};

      const byItem: Record<string, PncpUltimoInfo> = {};
      const nextLastQuotes: Record<string, string> = {};
      for (const it of items || []) {
        const c = String(it.catmat || "").trim();
        const info = byCatmat[c];
        if (info) {
          byItem[it.item] = info;
          // Pré-preenche o input do último licitado com o último resultado unitário, se existir
          if (
            typeof (info as any).valor_unitario_resultado_num === "number" &&
            Number.isFinite((info as any).valor_unitario_resultado_num)
          ) {
            nextLastQuotes[it.item] = fmtSmart((info as any).valor_unitario_resultado_num);
          }
        }
      }

      setPncpUltimoByItem(byItem);
      setLastQuotes((prev) => ({ ...nextLastQuotes, ...prev }));
    } finally {
      setPncpUltimoLoading(false);
    }
  }

  async function openPncpHistorico(catmat: string) {
    const c = String(catmat || "").trim();
    if (!c || !/^\d+$/.test(c)) return;
    setPncpHistOpen(true);
    setPncpHistCatmat(c);
    setPncpHistError("");
    setPncpHistRows([]);
    setPncpHistLoading(true);
    try {
      const res = await fetch(`/api/catmat_historico?catmat=${encodeURIComponent(c)}`);
      if (!res.ok) {
        const msg = await res.text();
        setPncpHistError(msg || "Falha ao consultar histórico.");
        return;
      }
      const data = await res.json();
      setPncpHistRows((data.rows || []) as PncpHistoricoRow[]);
    } catch (e: any) {
      setPncpHistError(String(e));
    } finally {
      setPncpHistLoading(false);
    }
  }

  function closePncpHistorico() {
    setPncpHistOpen(false);
    setPncpHistCatmat("");
    setPncpHistError("");
    setPncpHistRows([]);
  }

  async function loadPreview() {
    if (!file) {
      setStatus("Selecione um PDF primeiro.");
      return;
    }
    setStatus("Gerando prévia...");
    setLoadingPreview(true);
    setPreviewReady(false);
    setPreview([]);
    setOverrides({});
    setLastQuotes({});
    setPncpUltimoByItem({});

    try {
      const form = new FormData();
      form.append("file", file);

      const res = await fetch("/api/preview", {
        method: "POST",
        body: form,
      });

      if (!res.ok) {
        const msg = await res.text();
        setStatus(`Falha ao gerar prévia:\n${msg}`);
        return;
      }

      const data = await res.json();
      const items: PreviewItem[] = data?.items || [];
      setPreview(items);
      setPreviewReady(true);
      setStatus("Prévia gerada.");

      // PNCP auto-fill
      await hydratePncpUltimo(items);

      setStepPreviewDone(true);
    } catch (e: any) {
      setStatus(`Falha ao gerar prévia:\n${String(e)}`);
    } finally {
      setLoadingPreview(false);
    }
  }

  const tableRows = useMemo(() => {
    const rows = preview.map((it) => {
      const ov = overrides[it.item];
      const finalValue = ov?.modo === "Manual" ? computeManualFinal(it, ov) : it.valor_final;

      // A.F. em manual deve ser o número de não deselecionados (selecionados)
      const nFinalManual = ov?.modo === "Manual" ? ov.selecionados.length : it.n_final;

      return {
        ...it,
        n_final: nFinalManual,
        modo: ov?.modo === "Manual" ? "Manual" : it.modo === "Auto" ? "Automático" : it.modo,
        valor_final: finalValue,
      };
    });
    return rows;
  }, [preview, overrides]);

  function computeManualFinal(item: PreviewItem, ov: OverrideData): number | null {
    const rep = item.rep || {};
    const vals: number[] = Array.isArray(rep.vals) ? rep.vals : [];
    const selectedVals = ov.selecionados
      .map((idx) => Number(vals[idx]))
      .filter((v) => Number.isFinite(v));
    if (!selectedVals.length) return null;
    // Por padrão (manual): média simples dos selecionados
    const mean = selectedVals.reduce((a, b) => a + b, 0) / selectedVals.length;
    return mean;
  }

  const canGenerate = useMemo(() => {
    if (!file) return false;
    if (!preview.length) return false;
    if (!numeroLista.trim() || !nomeLista.trim() || !processoSEI.trim() || !responsavel.trim()) return false;
    return true;
  }, [file, preview, numeroLista, nomeLista, processoSEI, responsavel]);

  async function generateZip() {
    if (!file) {
      setStatus("Selecione um PDF primeiro.");
      return;
    }
    if (!preview.length) {
      setStatus("Gere a prévia antes de gerar o ZIP.");
      return;
    }
    if (!numeroLista.trim() || !nomeLista.trim() || !processoSEI.trim() || !responsavel.trim()) {
      setStatus("Preencha os campos obrigatórios (Lista/SEI/Responsável).");
      return;
    }

    setLoadingGenerate(true);
    setStatus("Gerando ZIP...");

    try {
      const payload = {
        numero_lista: numeroLista.trim(),
        nome_lista: nomeLista.trim(),
        processo_sei: processoSEI.trim(),
        responsavel: responsavel.trim(),
        preview_items: preview,
        overrides,
        last_quotes: lastQuotes,
      };

      const res = await fetch("/api/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        const msg = await res.text();
        setStatus(`Falha ao processar: ${msg}`);
        return;
      }

      const blob = await res.blob();
      const url = window.URL.createObjectURL(blob);

      const a = document.createElement("a");
      a.href = url;
      a.download = `Formacao_Precos_Referencia_Lista_${safeSlug(numeroLista)}.zip`;
      document.body.appendChild(a);
      a.click();
      a.remove();

      window.URL.revokeObjectURL(url);
      setStatus("ZIP gerado.");
    } catch (e: any) {
      setStatus(`Falha ao gerar ZIP: ${String(e)}`);
    } finally {
      setLoadingGenerate(false);
    }
  }

  // === UX: botões em foco ===
  const highlightChoose = !file;
  const highlightPreview = !!file && !preview.length;

  function reqBorder(v: string): string {
    return v.trim() ? "#9ca3af" : "#b91c1c";
  }
  function reqBg(v: string): string {
    return v.trim() ? "#ffffff" : "#fef2f2";
  }

  const pncpUltForModal = useMemo(() => {
    const cat = pncpHistCatmat;
    const it = preview.find((x) => String(x.catmat || "") === cat);
    if (!it) return null;
    return pncpUltimoByItem[it.item] || null;
  }, [pncpHistCatmat, preview, pncpUltimoByItem]);

  return (
    <main style={{ margin: "12px 0 0", padding: "0 0 110px" }}>
      {/* Header / orientação */}
      <div style={{ marginTop: 4, marginBottom: 10 }}>
        <div style={{ fontSize: 18, fontWeight: 900, color: "#111827" }}>
          UPDE — Preços de Referência (Prévia + Ajuste Manual)
        </div>
        <div style={{ color: "#6b7280", marginTop: 4, fontSize: 13 }}>
          1) Faça upload do PDF do ComprasGOV → 2) Veja a prévia → 3) Informe o último licitado → 4) Ajuste manual (liberado
          quando Valor calculado ≤ 1,2× Último licitado) → 5) Gere o ZIP.
        </div>
      </div>

      {/* Upload / ações principais */}
      <div
        style={{
          display: "flex",
          gap: 12,
          alignItems: "center",
          justifyContent: "space-between",
          flexWrap: "wrap",
          padding: "10px 12px",
          border: "1px solid #e5e7eb",
          borderRadius: 12,
        }}
      >
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <button
            className={`btn ${highlightChoose ? "btnCta" : "btnPrimary"}`}
            onClick={() => fileInputRef.current?.click()}
          >
            Escolher arquivo
          </button>
          <input
            ref={fileInputRef}
            type="file"
            accept="application/pdf"
            style={{ display: "none" }}
            onChange={(e) => {
              const f = e.target.files?.[0] || null;
              setFile(f);
              setStatus("");
              setPreview([]);
              setPreviewReady(false);
              setOverrides({});
              setLastQuotes({});
              setPncpUltimoByItem({});
              setStepUploadDone(!!f);
              setStepPreviewDone(false);
            }}
          />
          <div style={{ color: "#374151", fontSize: 13 }}>
            {file ? file.name : "Nenhum arquivo escolhido"}
          </div>
        </div>

        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <button
            onClick={loadPreview}
            disabled={!file || loadingPreview || stepPreviewDone}
            className={`btn ${highlightPreview ? "btnCta" : "btnPrimary"}`}
          >
            {loadingPreview ? "Gerando..." : stepPreviewDone ? "Prévia gerada" : "Gerar prévia"}
          </button>
        </div>
      </div>

      {/* Campos obrigatórios */}
      {preview.length > 0 && (
        <div
          style={{
            marginTop: 12,
            border: "1px solid #e5e7eb",
            borderRadius: 12,
            padding: "10px 12px",
            background: "#f3f4f6",
          }}
        >
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
            <div style={{ fontWeight: 900 }}>Dados do relatório</div>

            <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", marginLeft: "auto" }}>
              <input
                value={numeroLista}
                onChange={(e) => setNumeroLista(e.target.value)}
                placeholder="Número da Lista"
                style={{
                  width: 160,
                  fontSize: 14,
                  padding: "6px 8px",
                  border: `1px solid ${reqBorder(numeroLista)}` as any,
                  background: reqBg(numeroLista),
                  borderRadius: 6,
                }}
              />
              <input
                value={nomeLista}
                onChange={(e) => setNomeLista(e.target.value)}
                placeholder="Nome da Lista"
                style={{
                  width: 260,
                  fontSize: 14,
                  padding: "6px 8px",
                  border: `1px solid ${reqBorder(nomeLista)}` as any,
                  background: reqBg(nomeLista),
                  borderRadius: 6,
                }}
              />
              <input
                value={processoSEI}
                onChange={(e) => setProcessoSEI(e.target.value)}
                placeholder="Processo SEI"
                style={{
                  width: 200,
                  fontSize: 14,
                  padding: "6px 8px",
                  border: `1px solid ${reqBorder(processoSEI)}` as any,
                  background: reqBg(processoSEI),
                  borderRadius: 6,
                }}
              />
              <input
                value={responsavel}
                onChange={(e) => setResponsavel(e.target.value)}
                placeholder="Responsável"
                style={{
                  width: 220,
                  fontSize: 14,
                  padding: "6px 8px",
                  border: `1px solid ${reqBorder(responsavel)}` as any,
                  background: reqBg(responsavel),
                  borderRadius: 6,
                }}
              />
            </div>
            {(!numeroLista.trim() || !nomeLista.trim() || !processoSEI.trim() || !responsavel.trim()) && (
              <div style={{ color: "#c62828", fontWeight: 600, marginLeft: "auto" }}>
                Preencha os campos obrigatórios para liberar o ZIP.
              </div>
            )}
          </div>
        </div>
      )}

      {/* Status */}
      {status && (
        <pre
          style={{
            marginTop: 12,
            background: "#f9fafb",
            border: "1px solid #e5e7eb",
            borderRadius: 12,
            padding: 12,
            whiteSpace: "pre-wrap",
            fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace",
            fontSize: 12,
          }}
        >
          {status}
        </pre>
      )}

      {/* Tabela */}
      {preview.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <table
            style={{
              borderCollapse: "collapse",
              width: "100%",
              tableLayout: "fixed",
              fontSize: 14,
            }}
          >
            <colgroup>
              <col style={{ width: "8%" }} />
              <col style={{ width: "7%" }} />
              <col style={{ width: "8%" }} />
              <col style={{ width: "8%" }} />
              <col style={{ width: "8%" }} />
              <col style={{ width: "9%" }} />
              <col style={{ width: "10%" }} />
              <col style={{ width: "10%" }} />
              <col style={{ width: "7%" }} />
              <col style={{ width: "10%" }} />
              <col style={{ width: "9%" }} />
              <col style={{ width: "7%" }} />
            </colgroup>
            <thead>
              <tr>
                {[
                  "Item",
                  "Catmat",
                  "Entradas iniciais",
                  "Entradas finais",
                  "Excl. altos",
                  "Excl. inexequíveis",
                  "Valor calculado",
                  "Último licitado",
                  "Modo",
                  "Valor final",
                  "Dif. (R$)",
                  "Ajuste",
                ].map((h) => (
                  <th
                    key={h}
                    style={{
                      border: "1px solid #ddd",
                      padding: "8px 8px",
                      background: "#f7f7f7",
                      textAlign: "center",
                      whiteSpace: "normal",
                    }}
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {tableRows.map((r, rowIdx) => {
                const isActive = activeLastQuoteRow === r.item;
                const baseBg = rowIdx % 2 === 0 ? "#ffffff" : "#f4f4f4";
                const dif = computeDif(r.item, r.valor_final);
                const adj = getAdjustButtonState(r.item, r.valor_final);

                return (
                  <tr
                    key={r.item}
                    style={{
                      background: isActive ? "#e8f4ff" : baseBg,
                      boxShadow: isActive ? "inset 0 0 0 2px #1976d2" : undefined,
                      position: isActive ? "relative" : undefined,
                      transition: "background 120ms ease, box-shadow 120ms ease",
                    }}
                  >
                    <td style={{ border: "1px solid #ddd", padding: "8px 8px", textAlign: "center" }}>{r.item}</td>
                    <td style={{ border: "1px solid #ddd", padding: "8px 8px", textAlign: "center" }}>{r.catmat}</td>
                    <td style={{ border: "1px solid #ddd", padding: "8px 8px", textAlign: "center" }}>{r.n_bruto}</td>
                    <td style={{ border: "1px solid #ddd", padding: "8px 8px", textAlign: "center" }}>{r.n_final}</td>
                    <td style={{ border: "1px solid #ddd", padding: "8px 8px", textAlign: "center" }}>{r.excl_altos}</td>
                    <td style={{ border: "1px solid #ddd", padding: "8px 8px", textAlign: "center" }}>{r.excl_baixos}</td>
                    <td style={{ border: "1px solid #ddd", padding: "8px 8px", textAlign: "center" }}>
                      {fmtBRL(r.valor_calculado)}
                    </td>

                    <td style={{ border: "1px solid #ddd", padding: "6px 6px", textAlign: "center", minWidth: 170 }}>
                      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 4 }}>
                        <input
                          value={lastQuotes[r.item] || ""}
                          onChange={(e) => setLastQuotes((prev) => ({ ...prev, [r.item]: e.target.value }))}
                          onFocus={() => setActiveLastQuoteRow(r.item)}
                          onBlur={() => setActiveLastQuoteRow((prev) => (prev === r.item ? null : prev))}
                          placeholder="ex: 1.234,56"
                          style={{
                            width: 88,
                            fontSize: 13,
                            padding: "3px 6px",
                            border: isActive ? "2px solid #1976d2" : "1px solid #ccc",
                            borderRadius: 6,
                            outline: "none",
                            background: isActive ? "#ffffff" : undefined,
                          }}
                        />

                        <div
                          style={{
                            width: 150,
                            maxWidth: 150,
                            textAlign: "center",
                            fontSize: 11,
                            color: "#374151",
                            lineHeight: "14px",
                          }}
                        >
                          {(() => {
                            const info = pncpUltimoByItem[r.item];
                            if (pncpUltimoLoading) return "Consultando PNCP...";
                            if (!info) return "";

                            const pe = (info as any).pregao ? `PE ${(info as any).pregao}` : "";
                            const d = (info as any).data_resultado_br || "";

                            const line3 =
                              (info as any).status === "fracassado"
                                ? "FRACASSADO"
                                : (info as any).nome_fornecedor ||
                                  ((info as any).status === "nao_encontrado" ? "Sem registro" : "");

                            const lineStyle: React.CSSProperties = {
                              width: "100%",
                              overflow: "hidden",
                              textOverflow: "ellipsis",
                              whiteSpace: "nowrap",
                            };

                            return (
                              <>
                                {pe && (
                                  <div style={lineStyle} title={pe}>
                                    {pe}
                                  </div>
                                )}
                                {d && (
                                  <div style={lineStyle} title={d}>
                                    {d}
                                  </div>
                                )}
                                {line3 && (
                                  <div style={lineStyle} title={line3}>
                                    {line3}
                                  </div>
                                )}
                              </>
                            );
                          })()}
                        </div>

                        <button
                          className="btn btnGhost"
                          style={{ padding: "5px 10px", fontSize: 12, height: 28 }}
                          onClick={() => openPncpHistorico(String(r.catmat || ""))}
                          disabled={!r.catmat}
                        >
                          Histórico
                        </button>
                      </div>
                    </td>

                    <td style={{ border: "1px solid #ddd", padding: "8px 8px", textAlign: "center" }}>{r.modo}</td>
                    <td style={{ border: "1px solid #ddd", padding: "8px 8px", textAlign: "center" }}>
                      {fmtBRL(r.valor_final)}
                    </td>
                    <td style={{ border: "1px solid #ddd", padding: "8px 8px", textAlign: "center" }}>
                      {dif === null ? "" : fmtBRL(dif)}
                    </td>

                    <td style={{ border: "1px solid #ddd", padding: "8px 8px", textAlign: "center" }}>
                      <div style={{ display: "flex", flexDirection: "column", gap: 6, alignItems: "center" }}>
                        <button
                          onClick={() => openManualModal(r)}
                          disabled={!adj.enabled}
                          style={{
                            padding: "8px 12px",
                            borderRadius: 10,
                            border:
                              adj.color === "green"
                                ? "1px solid #1b5e20"
                                : adj.color === "red"
                                ? "1px solid #7f1d1d"
                                : adj.color === "yellow"
                                ? "1px solid #8a6a00"
                                : "1px solid #d1d5db",
                            fontWeight: 700,
                            background:
                              adj.color === "green"
                                ? "#2e7d32"
                                : adj.color === "red"
                                ? "#c62828"
                                : adj.color === "yellow"
                                ? "#f1c232"
                                : "#f3f4f6",
                            color:
                              adj.color === "yellow" ? "#111827" : adj.color === "disabled" ? "#6b7280" : "white",
                            cursor: adj.enabled ? "pointer" : "not-allowed",
                            minWidth: 92,
                          }}
                          title={
                            adj.color === "red"
                              ? "Último licitado > Valor final (ajuste recomendado)"
                              : adj.color === "yellow"
                              ? "Disponível quando Valor calculado ≤ 1,2× Último licitado"
                              : adj.color === "green"
                              ? "Ajustado manualmente"
                              : "Só disponível quando Valor calculado ≤ 1,2× Último licitado"
                          }
                        >
                          Ajustar
                        </button>

                        {overrides[r.item]?.modo === "Manual" && (
                          <button className="btn btnGhost" onClick={() => clearManualOverride(r.item)}>
                            Remover
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Modal ajuste manual */}
      {manualModal.open && (
        <div
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0,0,0,0.45)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            padding: 16,
            zIndex: 60,
          }}
          onClick={(e) => {
            if (e.target === e.currentTarget) closeModal();
          }}
        >
          <div
            style={{
              width: "min(1100px, 100%)",
              background: "white",
              borderRadius: 8,
              padding: 16,
              maxHeight: "85vh",
              overflowY: "auto",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "flex-start" }}>
              <div>
                <h2 style={{ margin: 0 }}>Ajuste manual — {manualModal.itemId}</h2>
                <div style={{ marginTop: 6, color: "#6b7280", fontSize: 13 }}>
                  Selecione os valores que devem compor a estimativa e justifique a análise manual.
                </div>
              </div>
              <button className="btn btnGhost" onClick={closeModal}>
                Fechar
              </button>
            </div>

            {/* Último licitado dentro do modal */}
            <div
              style={{
                marginTop: 12,
                display: "flex",
                justifyContent: "space-between",
                gap: 10,
                flexWrap: "wrap",
                padding: 10,
                border: "1px solid #e5e7eb",
                borderRadius: 10,
                background: "#f9fafb",
              }}
            >
              <div style={{ color: "#374151", fontSize: 13 }}>
                <strong>Último licitado (digitado):</strong>{" "}
                {lastQuotes[manualModal.itemId] ? `R$ ${lastQuotes[manualModal.itemId]}` : "—"}
              </div>

              {(() => {
                const stats = computeStatsFromSelected(manualModal.entries, manualModal.selected);
                const valorFinal = stats.mean;
                return (
                  <div style={{ color: "#111827", fontSize: 13 }}>
                    <strong>Valor estimado (dinâmico):</strong>{" "}
                    {valorFinal === null ? "—" : `R$ ${fmtSmart(valorFinal)}`}
                  </div>
                );
              })()}
            </div>

            {/* Lista de valores brutos */}
            <div style={{ marginTop: 12 }}>
              <div style={{ fontWeight: 900, marginBottom: 8 }}>Valores brutos (ordem crescente)</div>

              <div style={{ border: "1px solid #e5e7eb", borderRadius: 10, overflow: "hidden" }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                  <thead>
                    <tr style={{ background: "#f7f7f7" }}>
                      {["#", "Selecionar", "Valor (R$)", "Fonte"].map((h) => (
                        <th
                          key={h}
                          style={{
                            borderBottom: "1px solid #e5e7eb",
                            padding: "10px 10px",
                            textAlign: "center",
                            whiteSpace: "nowrap",
                            fontWeight: 800,
                          }}
                        >
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {manualModal.entries.map((e, i) => {
                      const isKept = manualModal.autoKept.has(e.idx);
                      const isExAltos = manualModal.autoExclAltos.has(e.idx);
                      const isExBaixos = manualModal.autoExclBaixos.has(e.idx);

                      const bg = isExAltos ? "#fdecea" : isExBaixos ? "#fff7cc" : i % 2 === 0 ? "#fff" : "#f9fafb";

                      return (
                        <tr key={e.idx} style={{ background: bg }}>
                          <td style={{ padding: "8px 10px", textAlign: "center" }}>{i + 1}</td>
                          <td style={{ padding: "8px 10px", textAlign: "center" }}>
                            <input
                              type="checkbox"
                              checked={manualModal.selected.has(e.idx)}
                              onChange={() => toggleSelect(e.idx)}
                            />
                          </td>
                          <td style={{ padding: "8px 10px", textAlign: "right", fontFamily: "monospace" }}>
                            {fmtSmart(e.valor)}
                          </td>
                          <td style={{ padding: "8px 10px" }}>
                            {e.fonte ? String(e.fonte) : isKept ? "—" : "—"}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              {/* Estatísticas dinâmicas */}
              <div
                style={{
                  marginTop: 12,
                  border: "1px solid #e5e7eb",
                  borderRadius: 10,
                  padding: 12,
                  background: "#f9fafb",
                }}
              >
                {(() => {
                  const stats = computeStatsFromSelected(manualModal.entries, manualModal.selected);

                  return (
                    <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
                      <div>
                        <div style={{ fontWeight: 800 }}>Número de valores selecionados</div>
                        <div style={{ fontFamily: "monospace" }}>{stats.n}</div>
                      </div>
                      <div>
                        <div style={{ fontWeight: 800 }}>Média (inclusão manual)</div>
                        <div style={{ fontFamily: "monospace" }}>
                          {stats.mean === null ? "—" : fmtSmart(stats.mean)}
                        </div>
                      </div>
                      <div>
                        <div style={{ fontWeight: 800 }}>Mediana (inclusão manual)</div>
                        <div style={{ fontFamily: "monospace" }}>
                          {stats.median === null ? "—" : fmtSmart(stats.median)}
                        </div>
                      </div>
                      <div>
                        <div style={{ fontWeight: 800 }}>Coeficiente de Variação (inclusão manual)</div>
                        <div style={{ fontFamily: "monospace" }}>
                          {stats.cv === null ? "—" : `${(stats.cv * 100).toFixed(2)}%`}
                        </div>
                      </div>
                      <div>
                        <div style={{ fontWeight: 800 }}>Valor Final (inclusão manual)</div>
                        <div style={{ fontFamily: "monospace" }}>
                          {stats.mean === null ? "—" : fmtSmart(stats.mean)}
                        </div>
                      </div>
                    </div>
                  );
                })()}
              </div>
            </div>

            {/* Justificativa */}
            <div style={{ marginTop: 12 }}>
              <div style={{ fontWeight: 900, marginBottom: 8 }}>Justificativa de análise manual</div>

              <select
                value={manualModal.justificativaSelected}
                onChange={(e) => {
                  const v = e.target.value;
                  setManualModal((prev) => ({
                    ...prev,
                    justificativaSelected: v,
                    justificativaOutro: v === "OUTRO" ? prev.justificativaOutro : "",
                  }));
                }}
                style={{
                  width: "100%",
                  padding: "10px 10px",
                  borderRadius: 10,
                  border: "1px solid #d1d5db",
                  fontSize: 14,
                  background: "white",
                }}
              >
                <option value="">Selecione...</option>
                <option value="Foi (Foram) excluída(s) a(s) cotação(ões) inferior(es) ao último preço homologado no HUSM para evitar a fixação de preço estimado potencialmente inexequível sob risco de fracasso do certame.">
                  Foi (Foram) excluída(s) a(s) cotação(ões) inferior(es) ao último preço homologado no HUSM para evitar a fixação de preço estimado potencialmente inexequível sob risco de fracasso do certame.
                </option>
                <option value="Foi (Foram) excluída(s) a(s) cotação(ões) inferior(es) ao último preço homologado no HUSM e discrepante da cotação do produto, cuja comercialização é exclusiva de fornecedor específico.">
                  Foi (Foram) excluída(s) a(s) cotação(ões) inferior(es) ao último preço homologado no HUSM e discrepante da cotação do produto, cuja comercialização é exclusiva de fornecedor específico.
                </option>
                <option value="OUTRO">Outro</option>
              </select>

              {manualModal.justificativaSelected === "OUTRO" && (
                <textarea
                  value={manualModal.justificativaOutro}
                  onChange={(e) => setManualModal((prev) => ({ ...prev, justificativaOutro: e.target.value }))}
                  placeholder="Digite a justificativa..."
                  style={{
                    width: "100%",
                    minHeight: 90,
                    marginTop: 10,
                    padding: "10px 10px",
                    borderRadius: 10,
                    border: "1px solid #d1d5db",
                    fontSize: 14,
                    resize: "vertical",
                  }}
                />
              )}
            </div>

            <div style={{ marginTop: 14, display: "flex", justifyContent: "flex-end", gap: 10 }}>
              <button className="btn btnGhost" onClick={closeModal}>
                Cancelar
              </button>
              <button className="btn btnPrimary" onClick={saveManualOverride}>
                Salvar ajuste
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Modal Histórico PNCP */}
      {pncpHistOpen && (
        <div
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0,0,0,0.45)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            padding: 16,
            zIndex: 55,
          }}
          onClick={(e) => {
            if (e.target === e.currentTarget) closePncpHistorico();
          }}
        >
          <div
            style={{
              width: "min(1050px, 100%)",
              background: "white",
              borderRadius: 8,
              padding: 16,
              maxHeight: "85vh",
              overflowY: "auto",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "flex-start" }}>
              <div>
                <h2 style={{ margin: 0 }}>Histórico PNCP — CATMAT {pncpHistCatmat}</h2>
                {pncpUltForModal && (
                  <div style={{ marginTop: 6, color: "#374151", fontSize: 13, lineHeight: 1.35 }}>
                    <strong>Último registro:</strong>{" "}
                    {(pncpUltForModal as any).status === "ok"
                      ? `R$ ${fmtSmart((pncpUltForModal as any).valor_unitario_resultado_num)}`
                      : (pncpUltForModal as any).status === "fracassado"
                      ? "Fracassado"
                      : "Sem registro"}
                    {(pncpUltForModal as any).pregao ? ` | Pregão ${(pncpUltForModal as any).pregao}` : ""}
                    {(pncpUltForModal as any).nome_fornecedor ? ` | ${(pncpUltForModal as any).nome_fornecedor}` : ""}
                    {(pncpUltForModal as any).data_resultado_br ? ` | ${(pncpUltForModal as any).data_resultado_br}` : ""}
                  </div>
                )}
              </div>
              <button className="btn btnGhost" onClick={closePncpHistorico}>
                Fechar
              </button>
            </div>

            {pncpHistLoading && <div style={{ marginTop: 12 }}>Carregando histórico...</div>}
            {!!pncpHistError && (
              <div style={{ marginTop: 12, color: "#b91c1c", whiteSpace: "pre-wrap" }}>{pncpHistError}</div>
            )}

            {!pncpHistLoading && !pncpHistError && (
              <div style={{ marginTop: 12 }}>
                <div style={{ fontSize: 13, color: "#6b7280", marginBottom: 8 }}>
                  {pncpHistRows.length} registro(s) encontrado(s).
                </div>
                <div style={{ overflowX: "auto", border: "1px solid #e5e7eb", borderRadius: 10 }}>
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                    <thead>
                      <tr style={{ background: "#f7f7f7" }}>
                        {[
                          "Seq.",
                          "Data",
                          "Pregão",
                          "Nº Item PE",
                          "Situação",
                          "Fornecedor",
                          "Link",
                          "Valor Estimado (R$)",
                          "Valor Licitado (R$)",
                        ].map((h) => (
                          <th
                            key={h}
                            style={{
                              borderBottom: "1px solid #e5e7eb",
                              padding: "10px 10px",
                              textAlign: "center",
                              whiteSpace: "nowrap",
                              fontWeight: 800,
                            }}
                          >
                            {h}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {pncpHistRows.map((r, i) => (
                        <tr key={`${r.seq}-${r.pregao}-${i}`} style={{ background: i % 2 === 0 ? "#ffffff" : "#f9fafb" }}>
                          <td style={{ padding: "8px 10px", textAlign: "center" }}>{r.seq ?? i + 1}</td>
                          <td style={{ padding: "8px 10px", textAlign: "center" }}>{r.data_resultado_br || "-"}</td>
                          <td style={{ padding: "8px 10px", textAlign: "center" }}>{r.pregao || "-"}</td>
                          <td style={{ padding: "8px 10px", textAlign: "center" }}>
                            {typeof r.numero_item_pncp === "number" ? r.numero_item_pncp : r.numero_item_pncp ?? "-"}
                          </td>
                          <td style={{ padding: "8px 10px" }}>{r.situacao || "-"}</td>
                          <td style={{ padding: "8px 10px" }}>{r.fornecedor || "-"}</td>
                          <td style={{ padding: "8px 10px", textAlign: "center" }}>
                            {r.link ? (
                              <a href={r.link} target="_blank" rel="noreferrer" style={{ textDecoration: "underline" }}>
                                Abrir
                              </a>
                            ) : (
                              "-"
                            )}
                          </td>
                          <td style={{ padding: "8px 10px", textAlign: "right", fontFamily: "monospace" }}>
                            {typeof r.valor_estimado_num === "number" ? fmtSmart(r.valor_estimado_num) : "-"}
                          </td>
                          <td style={{ padding: "8px 10px", textAlign: "right", fontFamily: "monospace" }}>
                            {typeof r.valor_licitado_num === "number" ? fmtSmart(r.valor_licitado_num) : "Fracassado"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Barra fixa de ações */}
      <div
        style={{
          position: "fixed",
          left: 0,
          right: 0,
          bottom: 0,
          background: "rgba(255,255,255,0.92)",
          borderTop: "1px solid #e5e7eb",
          padding: "10px 6px",
          backdropFilter: "blur(6px)",
        }}
      >
        <div
          style={{
            maxWidth: 1400,
            margin: "0 auto",
            display: "flex",
            gap: 10,
            justifyContent: "space-between",
            alignItems: "center",
            flexWrap: "wrap",
          }}
        >
          <div style={{ color: "#4b5563", fontSize: 13 }}>
            {loadingGenerate
              ? "Gerando ZIP..."
              : loadingPreview
              ? "Gerando prévia..."
              : preview.length
              ? "Prévia pronta. Preencha os campos obrigatórios e gere o ZIP."
              : file
              ? "Arquivo selecionado. Gere a prévia para continuar."
              : "Selecione um PDF para começar."}
          </div>

          <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
            <button
              onClick={generateZip}
              disabled={
                !file ||
                !preview.length ||
                loadingGenerate ||
                !numeroLista.trim() ||
                !nomeLista.trim() ||
                !processoSEI.trim() ||
                !responsavel.trim()
              }
              className={`btn ${canGenerate ? "btnCta" : "btnPrimary"}`}
              title={
                !preview.length ? "Gere a prévia primeiro" : canGenerate ? "Baixar ZIP (PDFs)" : "Preencha os campos obrigatórios"
              }
            >
              {loadingGenerate ? "Gerando..." : canGenerate ? "Baixar ZIP (PDFs)" : "Gerar ZIP (PDFs)"}
            </button>

            {SHOW_DEBUG && (
              <button
                onClick={async () => {
                  if (!file) {
                    setStatus("Selecione um PDF primeiro.");
                    return;
                  }
                  setStatus("Gerando debug...");
                  const form = new FormData();
                  form.append("file", file);
                  const res = await fetch("/api/debug", { method: "POST", body: form });
                  if (!res.ok) {
                    const msg = await res.text();
                    setStatus(`Falha ao gerar debug: ${msg}`);
                    return;
                  }
                  const blob = await res.blob();
                  const url = window.URL.createObjectURL(blob);
                  const a = document.createElement("a");
                  a.href = url;
                  a.download = "debug_audit.txt";
                  document.body.appendChild(a);
                  a.click();
                  a.remove();
                  window.URL.revokeObjectURL(url);
                  setStatus("Debug baixado.");
                }}
                disabled={!file}
                className="btn"
              >
                Debug (TXT)
              </button>
            )}
          </div>
        </div>
      </div>
    </main>
  );
}
