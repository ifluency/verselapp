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
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12, flexWrap: "wrap" }}>
        <div>
          <h1 style={{ fontSize: 20, margin: "12px 0 6px", fontWeight: 800 }}>
            Análise de Preços – UPDE (HUSM/UFSM)
          </h1>
          <div style={{ color: "#4b5563", fontSize: 13 }}>
            Fluxo: Upload → Prévia → Último licitado (PNCP) → Ajuste manual → ZIP (2 PDFs)
          </div>
        </div>

        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <label className="btn btnPrimary" style={{ cursor: "pointer" }}>
            Escolher PDF
            <input
              type="file"
              accept="application/pdf"
              style={{ display: "none" }}
              onChange={(e) => {
                const f = e.target.files?.[0] || null;
                setFile(f);
                setPreview([]);
                setPreviewReady(false);
                setOverrides({});
                setLastQuotes({});
                setStatus("");
              }}
            />
          </label>

          <button
            type="button"
            className="btn btnSecondary"
            disabled={!file || loadingPreview}
            onClick={loadPreview}
            style={{
              opacity: !file || loadingPreview ? 0.6 : 1,
            }}
          >
            {loadingPreview ? "Gerando..." : "Gerar prévia"}
          </button>

          <button
            type="button"
            className="btn btnSuccess"
            disabled={!canGenerate || loadingGenerate}
            onClick={generateZip}
            style={{
              opacity: !canGenerate || loadingGenerate ? 0.6 : 1,
            }}
          >
            {loadingGenerate ? "Gerando ZIP..." : "Baixar ZIP (2 PDFs)"}
          </button>
        </div>
      </div>

      <div style={{ marginTop: 10, marginBottom: 10, color: "#111827", fontSize: 13 }}>
        {status}
      </div>

      <div
        style={{
          display: "flex",
          gap: 12,
          flexWrap: "wrap",
          alignItems: "flex-end",
          marginBottom: 10,
          padding: "10px 10px",
          border: "1px solid #e5e7eb",
          borderRadius: 10,
          background: "#fbfbfb",
        }}
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <div style={{ fontSize: 12, color: "#6b7280" }}>Número da Lista (obrigatório)</div>
          <input
            value={numeroLista}
            onChange={(e) => setNumeroLista(e.target.value)}
            placeholder="ex: 001/2026"
            className="input"
            style={{ width: 160 }}
          />
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 6, flex: 1, minWidth: 240 }}>
          <div style={{ fontSize: 12, color: "#6b7280" }}>Nome da Lista (obrigatório)</div>
          <input
            value={nomeLista}
            onChange={(e) => setNomeLista(e.target.value)}
            placeholder="ex: Materiais hospitalares"
            className="input"
            style={{ width: "100%" }}
          />
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <div style={{ fontSize: 12, color: "#6b7280" }}>Processo SEI (obrigatório)</div>
          <input
            value={processoSEI}
            onChange={(e) => setProcessoSEI(e.target.value)}
            placeholder="ex: 00000.000000/2026-00"
            className="input"
            style={{ width: 220 }}
          />
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <div style={{ fontSize: 12, color: "#6b7280" }}>Responsável (obrigatório)</div>
          <input
            value={responsavel}
            onChange={(e) => setResponsavel(e.target.value)}
            placeholder="ex: Nome completo"
            className="input"
            style={{ width: 220 }}
          />
        </div>

        {!canGenerate && preview.length > 0 && (
          <div style={{ color: "#c62828", fontWeight: 600, marginLeft: "auto" }}>
            Preencha os campos obrigatórios para liberar o ZIP.
          </div>
        )}
      </div>

      {previewReady && preview.length > 0 && (
        <div style={{ overflowX: "auto", border: "1px solid #e5e7eb", borderRadius: 12 }}>
          <div style={{ padding: "8px 10px", background: "#f8fafc", borderBottom: "1px solid #e5e7eb" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
              <div style={{ fontWeight: 800 }}>Prévia</div>
              <span style={{ fontSize: 12, color: "#4b5563", maxWidth: 360, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {file?.name || ""}
              </span>
              <div style={{ marginLeft: "auto", fontSize: 12, color: "#4b5563" }}>
                Itens: <b>{preview.length}</b>
              </div>
            </div>
          </div>

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
              <col style={{ width: "7%" }} />
              <col style={{ width: "7%" }} />
              <col style={{ width: "7%" }} />
              <col style={{ width: "8%" }} />
              <col style={{ width: "9%" }} />
              {/* Último licitado precisa de mais espaço para fornecedor (dados carregam após a prévia) */}
              <col style={{ width: "14%" }} />
              <col style={{ width: "6%" }} />
              <col style={{ width: "9%" }} />
              <col style={{ width: "8%" }} />
              <col style={{ width: "10%" }} />
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
                  <td style={{ border: "1px solid #ddd", padding: "6px 6px", textAlign: "center", minWidth: 230 }}>
                    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 4 }}>
                      <input
                        value={lastQuotes[r.item] || ""}
                        onChange={(e) =>
                          setLastQuotes((prev) => ({ ...prev, [r.item]: e.target.value }))
                        }
                        onFocus={() => setActiveLastQuoteRow(r.item)}
                        onBlur={() =>
                          setActiveLastQuoteRow((prev) => (prev === r.item ? null : prev))
                        }
                        placeholder="ex: 1.234,56"
                        style={{
                          width: 104,
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
                          width: 200,
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

                          const pe = info.pregao ? `PE ${info.pregao}` : "";
                          const d = info.data_resultado_br || "";

                          const line3 =
                            info.status === "fracassado"
                              ? "FRACASSADO"
                              : info.nome_fornecedor ||
                                (info.status === "nao_encontrado" ? "Sem registro" : "");

                          return (
                            <>
                              {pe && <div style={LAST_LIC_LINE_STYLE} title={pe}>{pe}</div>}
                              {d && <div style={LAST_LIC_LINE_STYLE} title={d}>{d}</div>}
                              {line3 && (
                                <div
                                  style={{
                                    width: "100%",
                                    overflow: "hidden",
                                    textOverflow: "ellipsis",
                                    whiteSpace: "nowrap",
                                  }}
                                  title={line3}
                                >
                                  {line3}
                                </div>
                              )}
                            </>
                          );
                        })()}
                      </div>

                      <button
                        type="button"
                        className="btn btnGhost"
                        onClick={() => openPncpHistorico(r.catmat)}
                        style={{
                          padding: "1px 10px",
                          fontSize: 11,
                          lineHeight: "18px",
                          height: 22,
                          borderRadius: 6,
                        }}
                      >
                        Histórico
                      </button>
                    </div>
                  </td>

                  <td style={{ border: "1px solid #ddd", padding: "8px 8px", textAlign: "center" }}>
                    {r.method}
                  </td>

                  <td style={{ border: "1px solid #ddd", padding: "8px 8px", textAlign: "center" }}>
                    {fmtBRL(r.valor_final)}
                    <div style={{ fontSize: 11, color: "#6b7280" }}>
                      CV: {pct2(r.cv_val)}
                    </div>
                  </td>

                  <td style={{ border: "1px solid #ddd", padding: "8px 8px", textAlign: "center" }}>
                    {fmtSmart(r.dif)}
                  </td>

                  <td style={{ border: "1px solid #ddd", padding: "8px 8px", textAlign: "center" }}>
                    <button
                      type="button"
                      className="btn btnWarning"
                      onClick={() => openManualFor(r.item)}
                      style={{
                        padding: "6px 10px",
                        fontSize: 12,
                        borderRadius: 8,
                        background: r.has_override ? "#16a34a" : "#f59e0b",
                        color: "#fff",
                        border: "none",
                        cursor: "pointer",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {r.has_override ? "Ajustado" : "Ajustar"}
                    </button>
                  </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Modal PNCP Histórico */}
      {pncpHistOpen && (
        <div
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0,0,0,0.35)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            padding: 18,
            zIndex: 60,
          }}
          onClick={closePncpHistorico}
        >
          <div
            style={{
              width: "min(1100px, 98vw)",
              maxHeight: "85vh",
              background: "#fff",
              borderRadius: 12,
              boxShadow: "0 10px 30px rgba(0,0,0,0.25)",
              overflow: "hidden",
              display: "flex",
              flexDirection: "column",
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <div
              style={{
                padding: "12px 14px",
                borderBottom: "1px solid #e5e7eb",
                background: "#f8fafc",
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: 10,
              }}
            >
              <div>
                <div style={{ fontWeight: 900, fontSize: 15 }}>
                  Histórico PNCP — CATMAT {pncpHistCatmat}
                </div>
                <div style={{ fontSize: 12, color: "#6b7280" }}>
                  Itens mais recentes (criterio_julgamento_id_pncp = 1)
                </div>
              </div>
              <button type="button" className="btn btnGhost" onClick={closePncpHistorico}>
                Fechar
              </button>
            </div>

            <div style={{ padding: 14, overflow: "auto" }}>
              {pncpHistLoading ? (
                <div style={{ color: "#4b5563" }}>Consultando histórico...</div>
              ) : pncpHistError ? (
                <div style={{ color: "#c62828", whiteSpace: "pre-wrap" }}>{pncpHistError}</div>
              ) : !pncpHistRows.length ? (
                <div style={{ color: "#4b5563" }}>Nenhum registro encontrado.</div>
              ) : (
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                  <thead>
                    <tr>
                      {[
                        "#",
                        "PE",
                        "Data",
                        "Fornecedor / Situação",
                        "Qtd",
                        "Estimado",
                        "Resultado",
                        "Link",
                      ].map((h) => (
                        <th
                          key={h}
                          style={{
                            border: "1px solid #e5e7eb",
                            background: "#f7f7f7",
                            padding: "8px 8px",
                            textAlign: "left",
                          }}
                        >
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {pncpHistRows.map((row, idx) => (
                      <tr key={`${row.id_compra_item}-${idx}`}>
                        <td style={{ border: "1px solid #e5e7eb", padding: "7px 8px" }}>
                          {idx + 1}
                        </td>
                        <td style={{ border: "1px solid #e5e7eb", padding: "7px 8px" }}>
                          {row.pregao ? `PE ${row.pregao}` : ""}
                        </td>
                        <td style={{ border: "1px solid #e5e7eb", padding: "7px 8px" }}>
                          {row.data_resultado_br || ""}
                        </td>
                        <td style={{ border: "1px solid #e5e7eb", padding: "7px 8px" }}>
                          <div style={{ fontWeight: 700 }}>
                            {row.resultado_status === "fracassado"
                              ? "FRACASSADO"
                              : row.nome_fornecedor || ""}
                          </div>
                          <div style={{ fontSize: 12, color: "#6b7280" }}>
                            {row.situacao_compra_item_nome || ""}
                          </div>
                        </td>
                        <td style={{ border: "1px solid #e5e7eb", padding: "7px 8px" }}>
                          {row.quantidade ?? ""}
                        </td>
                        <td style={{ border: "1px solid #e5e7eb", padding: "7px 8px" }}>
                          {fmtBRL(row.valor_unitario_estimado_num)}
                        </td>
                        <td style={{ border: "1px solid #e5e7eb", padding: "7px 8px" }}>
                          {row.resultado_status === "fracassado"
                            ? ""
                            : fmtBRL(row.valor_unitario_resultado_num)}
                        </td>
                        <td style={{ border: "1px solid #e5e7eb", padding: "7px 8px" }}>
                          {row.compra_link ? (
                            <a href={row.compra_link} target="_blank" rel="noreferrer">
                              Abrir
                            </a>
                          ) : (
                            ""
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Modal Ajuste Manual */}
      {manualOpen && (
        <div
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0,0,0,0.35)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            padding: 18,
            zIndex: 70,
          }}
          onClick={closeManual}
        >
          <div
            style={{
              width: "min(980px, 98vw)",
              maxHeight: "85vh",
              background: "#fff",
              borderRadius: 12,
              boxShadow: "0 10px 30px rgba(0,0,0,0.25)",
              overflow: "hidden",
              display: "flex",
              flexDirection: "column",
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <div
              style={{
                padding: "12px 14px",
                borderBottom: "1px solid #e5e7eb",
                background: "#f8fafc",
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: 10,
              }}
            >
              <div>
                <div style={{ fontWeight: 900, fontSize: 15 }}>
                  Ajuste manual — Item {manualItemId}
                </div>
                <div style={{ fontSize: 12, color: "#6b7280" }}>
                  Selecione quais valores entram no cálculo, método e justificativa.
                </div>
              </div>
              <button type="button" className="btn btnGhost" onClick={closeManual}>
                Fechar
              </button>
            </div>

            <div style={{ padding: 14, overflow: "auto" }}>
              {(() => {
                const it = preview.find((x) => x.item === manualItemId);
                if (!it) return null;

                const valsSorted = [...it.valores_brutos].sort((a, b) => a.valor - b.valor);

                const recomputeVals = valsSorted
                  .filter((v) => manualIncluded.includes(v.idx))
                  .map((v) => v.valor);

                const vMean = mean(recomputeVals);
                const vMedian = median(recomputeVals);
                const vCv = cv(recomputeVals);

                const lineStyle: React.CSSProperties = {
                  padding: "8px 10px",
                  borderBottom: "1px solid #eef2f7",
                  display: "flex",
                  alignItems: "center",
                  gap: 10,
                };

                return (
                  <>
                    <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 12 }}>
                      <div style={{ flex: 1, minWidth: 260, border: "1px solid #e5e7eb", borderRadius: 10, overflow: "hidden" }}>
                        <div style={{ background: "#f7f7f7", padding: "8px 10px", fontWeight: 900 }}>
                          Valores brutos (ordem crescente)
                        </div>
                        <div>
                          {valsSorted.map((v, i) => {
                            const isIncluded = manualIncluded.includes(v.idx);
                            const isAutoHigh = it.auto_excl_altos_idx.includes(v.idx);
                            const isAutoLow = it.auto_excl_baixos_idx.includes(v.idx);

                            const bg = isAutoHigh ? "#fee2e2" : isAutoLow ? "#fef9c3" : "#ffffff";

                            return (
                              <div key={v.idx} style={{ ...lineStyle, background: bg }}>
                                <input
                                  type="checkbox"
                                  checked={isIncluded}
                                  onChange={() => toggleInclude(v.idx)}
                                />
                                <div style={{ width: 28, textAlign: "right", color: "#6b7280" }}>
                                  {i + 1}
                                </div>
                                <div style={{ width: 110, fontWeight: 800 }}>{fmtSmart(v.valor)}</div>
                                <div style={{ flex: 1, color: "#374151" }}>{v.fonte}</div>
                              </div>
                            );
                          })}
                        </div>
                      </div>

                      <div style={{ width: 320, display: "flex", flexDirection: "column", gap: 10 }}>
                        <div style={{ border: "1px solid #e5e7eb", borderRadius: 10, padding: 12 }}>
                          <div style={{ fontWeight: 900, marginBottom: 6 }}>Recalcular</div>

                          <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 10 }}>
                            <label style={{ display: "flex", gap: 6, alignItems: "center" }}>
                              <input
                                type="radio"
                                checked={manualMethod === "mediana"}
                                onChange={() => setManualMethod("mediana")}
                              />
                              Mediana
                            </label>
                            <label style={{ display: "flex", gap: 6, alignItems: "center" }}>
                              <input
                                type="radio"
                                checked={manualMethod === "media"}
                                onChange={() => setManualMethod("media")}
                              />
                              Média
                            </label>
                          </div>

                          <div style={{ fontSize: 13, color: "#111827" }}>
                            <div>Valores selecionados: <b>{recomputeVals.length}</b></div>
                            <div>Média: <b>{fmtBRL(vMean)}</b></div>
                            <div>Mediana: <b>{fmtBRL(vMedian)}</b></div>
                            <div>CV: <b>{pct2(vCv)}</b></div>
                          </div>
                        </div>

                        <div style={{ border: "1px solid #e5e7eb", borderRadius: 10, padding: 12 }}>
                          <div style={{ fontWeight: 900, marginBottom: 6 }}>Justificativa</div>

                          <select
                            value={manualJust}
                            onChange={(e) => setManualJust(e.target.value)}
                            className="input"
                            style={{ width: "100%", marginBottom: 8 }}
                          >
                            <option value="PADRAO_1">Padrão 1</option>
                            <option value="PADRAO_2">Padrão 2</option>
                            <option value="OUTRO">Outro</option>
                          </select>

                          <div style={{ fontSize: 12, color: "#374151", marginBottom: 6 }}>
                            {manualJust === "OUTRO" ? "" : JUST_OPTIONS[manualJust] || ""}
                          </div>

                          {manualJust === "OUTRO" && (
                            <textarea
                              value={manualJustOther}
                              onChange={(e) => setManualJustOther(e.target.value)}
                              placeholder="Descreva a justificativa..."
                              className="input"
                              style={{ width: "100%", height: 90, resize: "vertical" }}
                            />
                          )}
                        </div>

                        <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
                          <button type="button" className="btn btnGhost" onClick={closeManual}>
                            Cancelar
                          </button>
                          <button type="button" className="btn btnSuccess" onClick={saveManual}>
                            Salvar ajuste
                          </button>
                        </div>
                      </div>
                    </div>
                  </>
                );
              })()}
            </div>
          </div>
        </div>
      )}

      {/* Footer fixo */}
      <div
        style={{
          position: "fixed",
          left: 0,
          right: 0,
          bottom: 0,
          background: "rgba(255,255,255,0.92)",
          borderTop: "1px solid #e5e7eb",
          padding: "8px 6px",
          backdropFilter: "blur(6px)",
        }}
      >
        <div
          style={{
            maxWidth: "100%",
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
              : "Selecione um PDF do ComprasGOV (Relatório Resumido)."}
          </div>

          <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
            <div style={{ fontSize: 12, color: "#6b7280" }}>
              CV exibido em % com 2 casas.
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
