"use client";

import React, { useEffect, useMemo, useState } from "react";

type PreviewItem = {
  item: string;
  catmat: string;
  n_bruto: number;
  n_final: number;
  excl_altos: number;
  excl_baixos: number;
  valor_calculado: number | null;
  valores_brutos: { idx: number; valor: number; fonte: string }[];
  auto_keep_idx: number[];
  auto_excl_altos_idx: number[];
  auto_excl_baixos_idx: number[];
};

type ManualOverride = {
  includedIndices: number[];
  method: "media" | "mediana";
  justificativaCodigo: string;
  justificativaTexto: string;
};

type PncpUltimoInfo = {
  catmat: string;
  status: "ok" | "fracassado" | "nao_encontrado";
  data_resultado_iso: string | null;
  data_resultado_br: string;
  id_compra: string;
  pregao: string;
  compra_link: string;
  nome_fornecedor: string;
  valor_unitario_estimado_num: number | null;
  valor_unitario_resultado_num: number | null;
};

type PncpHistoricoRow = {
  catmat: string;
  descricao_resumida: string;
  material_ou_servico: string;
  unidade_medida: string;
  id_compra: string;
  id_compra_item: string;
  numero_controle_pncp_compra: string;
  codigo_modalidade: number | null;
  data_resultado_iso: string | null;
  data_resultado_br: string;
  pregao: string;
  quantidade: number | null;
  valor_unitario_estimado_num: number | null;
  valor_unitario_resultado_num: number | null;
  resultado_status: "ok" | "fracassado";
  nome_fornecedor: string;
  situacao_compra_item_nome: string;
  compra_link: string;
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
  // Formato simples PT-BR: 1234.56 -> 1234,56 (sem milhares para manter consistente)
  return n.toFixed(2).replace(".", ",");
}

function fmtSmart(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return "";
  const dec = Math.abs(n) >= 1 ? 2 : 4;
  return n.toFixed(dec).replace(".", ",");
}

function mean(vals: number[]): number | null {
  if (!vals.length) return null;
  return vals.reduce((a, b) => a + b, 0) / vals.length;
}

function median(vals: number[]): number | null {
  if (!vals.length) return null;
  const s = [...vals].sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  if (s.length % 2 === 1) return s[mid];
  return (s[mid - 1] + s[mid]) / 2;
}

function cv(vals: number[]): number | null {
  const m = mean(vals);
  if (m === null || m === 0) return null;
  const variance = vals.reduce((acc, v) => acc + (v - m) * (v - m), 0) / vals.length;
  const std = Math.sqrt(variance);
  return std / m;
}

function pct2(x: number | null): string {
  if (x === null || !Number.isFinite(x)) return "";
  return (x * 100).toFixed(2).replace(".", ",") + "%";
}

const JUST_OPTIONS: Record<string, string> = {
  PADRAO_1:
    "Foi (Foram) excluída(s) a(s) cotação(ões) inferior(es) ao último preço homologado no HUSM para evitar a fixação de preço estimado potencialmente inexequível sob risco de fracasso do certame.",
  PADRAO_2:
    "Foi (Foram) excluída(s) a(s) cotação(ões) inferior(es) ao último preço homologado no HUSM e discrepante da cotação do produto, cuja comercialização é exclusiva de fornecedor específico.",
};

const LAST_LIC_LINE_STYLE: React.CSSProperties = {
  width: "100%",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

export default function PrecosPage() {
  const [file, setFile] = useState<File | null>(null);
  const [status, setStatus] = useState<string>("");
  const [loadingPreview, setLoadingPreview] = useState<boolean>(false);
  const [loadingGenerate, setLoadingGenerate] = useState<boolean>(false);
  const [previewReady, setPreviewReady] = useState<boolean>(false);
  const [preview, setPreview] = useState<PreviewItem[]>([]);
  const [lastQuotes, setLastQuotes] = useState<Record<string, string>>({});
  const [activeLastQuoteRow, setActiveLastQuoteRow] = useState<string | null>(null);

  const [numeroLista, setNumeroLista] = useState<string>("");
  const [nomeLista, setNomeLista] = useState<string>("");
  const [processoSEI, setProcessoSEI] = useState<string>("");
  const [responsavel, setResponsavel] = useState<string>("");

  const [overrides, setOverrides] = useState<Record<string, ManualOverride>>({});
  const [manualOpen, setManualOpen] = useState<boolean>(false);
  const [manualItemId, setManualItemId] = useState<string>("");
  const [manualMethod, setManualMethod] = useState<"media" | "mediana">("mediana");
  const [manualIncluded, setManualIncluded] = useState<number[]>([]);
  const [manualJust, setManualJust] = useState<string>("PADRAO_1");
  const [manualJustOther, setManualJustOther] = useState<string>("");

  const [pncpUltimoLoading, setPncpUltimoLoading] = useState<boolean>(false);
  const [pncpUltimoByItem, setPncpUltimoByItem] = useState<Record<string, PncpUltimoInfo>>({});

  const [pncpHistOpen, setPncpHistOpen] = useState<boolean>(false);
  const [pncpHistCatmat, setPncpHistCatmat] = useState<string>("");
  const [pncpHistLoading, setPncpHistLoading] = useState<boolean>(false);
  const [pncpHistError, setPncpHistError] = useState<string>("");
  const [pncpHistRows, setPncpHistRows] = useState<PncpHistoricoRow[]>([]);

  const tableRows = useMemo(() => {
    // Monta linhas já com o valor final considerando override (quando existir)
    return preview.map((it) => {
      const ov = overrides[it.item];
      const vals = it.valores_brutos
        .filter((v) => (ov ? ov.includedIndices.includes(v.idx) : it.auto_keep_idx.includes(v.idx)))
        .map((v) => v.valor);

      const method = ov?.method || "mediana";
      const valor_final =
        method === "media" ? mean(vals) : median(vals);

      const ultimo_digitado = parseBRL(lastQuotes[it.item] || "");
      const dif = ultimo_digitado !== null && valor_final !== null ? valor_final - ultimo_digitado : null;

      const cv_val = cv(vals);
      return {
        ...it,
        valor_final,
        dif,
        cv_val,
        method,
        has_override: Boolean(ov),
      };
    });
  }, [preview, overrides, lastQuotes]);

  const canGenerate = useMemo(() => {
    if (!preview.length) return false;
    if (!numeroLista.trim() || !nomeLista.trim() || !processoSEI.trim() || !responsavel.trim())
      return false;
    return true;
  }, [preview, numeroLista, nomeLista, processoSEI, responsavel]);

  async function hydratePncpUltimo(items: PreviewItem[]) {
    try {
      setPncpUltimoLoading(true);
      const catmats = Array.from(new Set(items.map((i) => i.catmat).filter(Boolean)));
      if (!catmats.length) return;

      const res = await fetch("/api/ultimo_licitado", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ catmats }),
      });

      if (!res.ok) {
        // Silencioso para não travar o fluxo (o usuário ainda pode digitar)
        return;
      }

      const data = await res.json();
      const by = (data.by_catmat || {}) as Record<string, PncpUltimoInfo>;

      const out: Record<string, PncpUltimoInfo> = {};
      for (const it of items) {
        const info = by[it.catmat];
        if (info) out[it.item] = info;
      }
      setPncpUltimoByItem(out);
    } catch {
      // ignore
    } finally {
      setPncpUltimoLoading(false);
    }
  }

  async function openPncpHistorico(catmat: string) {
    const c = String(catmat || "").trim();
    if (!c) return;

    setPncpHistOpen(true);
    setPncpHistCatmat(c);
    setPncpHistError("");
    setPncpHistRows([]);

    try {
      setPncpHistLoading(true);
      const res = await fetch(`/api/catmat_historico?catmat=${encodeURIComponent(c)}`);
      if (!res.ok) {
        const msg = await res.text();
        throw new Error(msg || `HTTP ${res.status}`);
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

    try {
      const form = new FormData();
      form.append("file", file);
      const res = await fetch("/api/preview", { method: "POST", body: form });
      if (!res.ok) {
        const msg = await res.text();
        setStatus(`Falha ao gerar prévia: ${msg}`);
        return;
      }
      const data = await res.json();
      const items = (data.items || []) as PreviewItem[];
      setPreview(items);
      // Consulta Neon (PNCP) para preencher automaticamente o "Último licitado" e mostrar contexto.
      await hydratePncpUltimo(items);
      setStatus(
        "Prévia carregada. Confira os dados PNCP e, se necessário, ajuste manualmente."
      );
      setPreviewReady(true);
    } catch (e: any) {
      setStatus(`Falha ao gerar prévia: ${String(e)}`);
    } finally {
      setLoadingPreview(false);
    }
  }

  async function generateZip() {
    if (!file) {
      setStatus("Selecione um PDF primeiro.");
      return;
    }
    if (!preview.length) {
      setStatus("Gere a prévia antes de baixar os arquivos.");
      return;
    }

    if (!numeroLista.trim() || !nomeLista.trim() || !processoSEI.trim() || !responsavel.trim()) {
      setStatus("Preencha os campos obrigatórios: Número da Lista, Nome da Lista, Processo SEI e Responsável.");
      return;
    }

    setStatus("Gerando arquivos finais...");
    setLoadingGenerate(true);

    // Payload para o backend
    const last_quotes: Record<string, number> = {};
    for (const it of preview) {
      const v = parseBRL(lastQuotes[it.item] || "");
      if (v !== null) last_quotes[it.item] = v;
    }

    const manual_overrides: any = {};
    for (const [itemId, ov] of Object.entries(overrides as Record<string, ManualOverride>)) {
      manual_overrides[itemId] = {
        included_indices: ov.includedIndices,
        method: ov.method,
        justificativa_codigo: ov.justificativaCodigo,
        justificativa_texto: ov.justificativaTexto,
      };
    }

    const payload = {
      last_quotes,
      manual_overrides,
      lista_meta: {
        numero_lista: numeroLista.trim(),
        nome_lista: nomeLista.trim(),
        processo_sei: processoSEI.trim(),
        responsavel: responsavel.trim(),
      },
    };

    try {
      const form = new FormData();
      form.append("file", file);
      form.append("payload", JSON.stringify(payload));

      const res = await fetch("/api/generate", { method: "POST", body: form });
      if (!res.ok) {
        const msg = await res.text();
        setStatus(`Falha ao processar: ${msg}`);
        return;
      }
      const blob = await res.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      const numeroSlug = safeSlug(numeroLista);
      a.download = `Formacao_Precos_Referencia_Lista_${numeroSlug}.zip`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);

      setStatus("Concluído! ZIP gerado com 2 PDFs.");
    } catch (e: any) {
      setStatus(`Falha ao gerar ZIP: ${String(e)}`);
    } finally {
      setLoadingGenerate(false);
    }
  }

  function openManualFor(itemId: string) {
    const it = preview.find((x) => x.item === itemId);
    if (!it) return;

    const existing = overrides[itemId];
    const initialIncluded = existing?.includedIndices?.length
      ? existing.includedIndices
      : it.auto_keep_idx;

    setManualItemId(itemId);
    setManualMethod(existing?.method || "mediana");
    setManualIncluded([...initialIncluded]);
    setManualJust(existing?.justificativaCodigo || "PADRAO_1");
    setManualJustOther(existing?.justificativaTexto || "");
    setManualOpen(true);
  }

  function closeManual() {
    setManualOpen(false);
    setManualItemId("");
    setManualIncluded([]);
    setManualJust("PADRAO_1");
    setManualJustOther("");
  }

  function toggleInclude(idx: number) {
    setManualIncluded((prev) =>
      prev.includes(idx) ? prev.filter((x) => x !== idx) : [...prev, idx]
    );
  }

  function saveManual() {
    if (!manualItemId) return;

    const justificativaCodigo = manualJust;
    const justificativaTexto =
      manualJust === "OUTRO" ? (manualJustOther || "").trim() : "";

    setOverrides((prev) => ({
      ...prev,
      [manualItemId]: {
        includedIndices: [...manualIncluded].sort((a, b) => a - b),
        method: manualMethod,
        justificativaCodigo,
        justificativaTexto,
      },
    }));
    closeManual();
  }

  return (
    <div style={{ paddingBottom: 120 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-
