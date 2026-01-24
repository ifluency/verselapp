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


export default function Page() {
  const [file, setFile] = useState<File | null>(null);
  const [status, setStatus] = useState<string>("");

  // Desativa "Gerar prévia" depois de gerar (reabilita ao trocar o arquivo)
  const [previewReady, setPreviewReady] = useState(false);

  // Campos obrigatórios para o PDF Tabela Comparativa de Valores
  const [numeroLista, setNumeroLista] = useState<string>("");
  const [nomeLista, setNomeLista] = useState<string>("");
  const [processoSEI, setProcessoSEI] = useState<string>("");

  const [responsavel, setResponsavel] = useState<string>("");

  const reqBg = (v: string) => (v.trim() ? "#ffffff" : "#f3f4f6");
  const reqBorder = (v: string) => (v.trim() ? "#cbd5e1" : "#ef4444");

  // Destaque visual: linha atualmente em edição do "Último licitado"
  const [activeLastQuoteRow, setActiveLastQuoteRow] = useState<string | null>(null);

  const [preview, setPreview] = useState<PreviewItem[]>([]);
  const [loadingPreview, setLoadingPreview] = useState(false);
  const [loadingGenerate, setLoadingGenerate] = useState(false);

  const [lastQuotes, setLastQuotes] = useState<Record<string, string>>({});
  const [overrides, setOverrides] = useState<Record<string, ManualOverride>>({});

  // Modal state
  const [modalItemId, setModalItemId] = useState<string | null>(null);
  const modalItem = useMemo(
    () => preview.find((p) => p.item === modalItemId) || null,
    [preview, modalItemId]
  );

  const [modalSelected, setModalSelected] = useState<number[]>([]);
  const [modalMethod, setModalMethod] = useState<"media" | "mediana">("media");
  const [modalJustCode, setModalJustCode] = useState<string>("");
  const [modalJustText, setModalJustText] = useState<string>("");

  // UI flags
  const SHOW_DEBUG = false; // ***desativado na UI (mantém a funcionalidade no backend)***
  const focusChooseFile = !file;
  const focusPreview = !!file && !previewReady;

  // Ao trocar o arquivo, reseta o fluxo e reabilita a prévia
  useEffect(() => {
    setPreview([]);
    setOverrides({});
    setLastQuotes({});
    setModalItemId(null);
    setActiveLastQuoteRow(null);
    setStatus("");
    setPreviewReady(false);
    setNumeroLista("");
    setNomeLista("");
    setProcessoSEI("");
    setResponsavel("");
  }, [file?.name, file?.size, file?.lastModified]);

  const modalIdxToVal = useMemo(() => {
    const m = new Map<number, number>();
    if (modalItem) {
      for (const e of modalItem.valores_brutos) {
        if (typeof e.idx === "number" && typeof e.valor === "number") {
          m.set(e.idx, e.valor);
        }
      }
    }
    return m;
  }, [modalItem]);

  const modalAutoExclAltos = useMemo(() => new Set(modalItem?.auto_excl_altos_idx || []), [modalItem]);
  const modalAutoExclBaixos = useMemo(() => new Set(modalItem?.auto_excl_baixos_idx || []), [modalItem]);

  const modalSortedEntries = useMemo(() => {
    if (!modalItem) return [] as { idx: number; valor: number; fonte: string }[];
    return [...modalItem.valores_brutos].sort((a, b) => a.valor - b.valor);
  }, [modalItem]);

  const modalIncludedValues = useMemo(() => {
    if (!modalItem) return [];
    const vals: number[] = [];
    for (const idx of modalSelected) {
      const v = modalIdxToVal.get(idx);
      if (typeof v === "number" && Number.isFinite(v)) vals.push(v);
    }
    return vals;
  }, [modalItem, modalSelected, modalIdxToVal]);

  const modalStats = useMemo(() => {
    const m = mean(modalIncludedValues);
    const med = median(modalIncludedValues);
    const c = cv(modalIncludedValues);
    const suggested = c === null ? "mediana" : c < 0.25 ? "media" : "mediana";
    const finalVal = modalMethod === "media" ? m : med;
    return { mean: m, median: med, cv: c, suggested, finalVal };
  }, [modalIncludedValues, modalMethod]);

  function closeModal() {
    setModalItemId(null);
    setModalSelected([]);
    setModalMethod("media");
    setModalJustCode("");
    setModalJustText("");
  }

  function openManualModal(item: PreviewItem) {
    // Só abre se elegível OU se já foi ajustado (para permitir reabrir/editar)
    const existing = overrides[item.item];
    const last = parseBRL(lastQuotes[item.item] || "");
    const calc = item.valor_calculado;
    const eligible = last !== null && last > 0 && calc !== null && calc <= 1.2 * last;
    if (!eligible && !existing) return;

    setModalItemId(item.item);

    // Inicializa com seleção sugerida pelo cálculo automático (mantidos)
    const suggestedIdx = (item.auto_keep_idx && item.auto_keep_idx.length)
      ? item.auto_keep_idx
      : item.valores_brutos.map((e) => e.idx);
    setModalSelected(suggestedIdx);

    // Se já existe override, carrega
    if (existing) {
      setModalSelected(existing.includedIndices);
      setModalMethod(existing.method);
      setModalJustCode(existing.justificativaCodigo);
      setModalJustText(existing.justificativaTexto);
    } else {
      // Sugere método pelo CV (mas o usuário escolhe)
      const baseCv = cv(item.valores_brutos.map((e) => e.valor));
      const suggested = baseCv === null ? "mediana" : baseCv < 0.25 ? "media" : "mediana";
      setModalMethod(suggested);
      setModalJustCode("");
      setModalJustText("");
    }
  }

  function toggleModalIndex(idx: number) {
    setModalSelected((prev) => {
      if (prev.includes(idx)) return prev.filter((x) => x !== idx);
      return [...prev, idx].sort((a, b) => a - b);
    });
  }

  function saveManualOverride() {
    if (!modalItem) return;
    if (!modalSelected.length) {
      setStatus("Selecione ao menos 1 valor para o cálculo manual.");
      return;
    }
    const finalJustText =
      modalJustCode && modalJustCode !== "OUTRO" ? JUST_OPTIONS[modalJustCode] || "" : modalJustText;

    setOverrides((prev) => ({
      ...prev,
      [modalItem.item]: {
        includedIndices: [...modalSelected].sort((a, b) => a - b),
        method: modalMethod,
        justificativaCodigo: modalJustCode,
        justificativaTexto: finalJustText,
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
      setPreview((data.items || []) as PreviewItem[]);
      setStatus("Prévia carregada. Preencha o último licitado e, se necessário, ajuste manualmente.");
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

  const canGenerate =
    !!file &&
    preview.length > 0 &&
    !loadingGenerate &&
    !!numeroLista.trim() &&
    !!nomeLista.trim() &&
    !!processoSEI.trim() &&
    !!responsavel.trim();

  const tableRows = useMemo(() => {
    return preview.map((it) => {
      const last = parseBRL(lastQuotes[it.item] || "");
      const calc = it.valor_calculado;
      const ov = overrides[it.item];

      const eligible = last !== null && last > 0 && calc !== null && calc <= 1.2 * last;
      const allowManual = eligible || !!ov;

      // Cores do botão quando elegível e ainda não ajustado:
      // - Vermelho: último licitado > valor calculado (valor calculado ficou abaixo do histórico)
      // - Amarelo: valor calculado está até 20% acima do último licitado (calc <= 1.2 * last)
      // - Verde: já ajustado (override salvo)
      let adjustColor: "red" | "yellow" | "none" | "green" = "none";
      if (ov) {
        adjustColor = "green";
      } else if (eligible && last !== null && calc !== null) {
        adjustColor = calc < last ? "red" : "yellow";
      }

      let modo = "Automático";
      let metodo = "";
      let valorFinal: number | null = calc;

      if (ov) {
        modo = "Manual";
        metodo = ov.method === "media" ? "Média" : "Mediana";
        const idxToVal = new Map<number, number>();
        for (const e of it.valores_brutos) idxToVal.set(e.idx, e.valor);
        const included = ov.includedIndices
          .map((idx) => idxToVal.get(idx))
          .filter((v) => typeof v === "number" && Number.isFinite(v)) as number[];
        valorFinal = ov.method === "media" ? mean(included) : median(included);
      }

      const diffAbs = last !== null && valorFinal !== null ? valorFinal - last : null;

      return {
        ...it,
        last,
        eligible,
        allowManual,
        adjustColor,
        modo,
        metodo,
        valorFinal,
        diffAbs,
        hasOverride: !!ov,
      };
    });
  }, [preview, lastQuotes, overrides]);

  return (
    <main style={{ maxWidth: 1200, margin: "12px auto", padding: "0 16px 110px" }}>
      <header
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          gap: 12,
          flexWrap: "wrap",
          padding: "10px 0 6px",
          borderBottom: "1px solid #e5e7eb",
        }}
      >
        <div>
          <h1 style={{ margin: 0 }}>Análise de Preços - UPDE</h1>
          <div style={{ marginTop: 4, color: "#4b5563" }}>
            Formação de preços de referência com base em pesquisa do ComprasGOV
          </div>
        </div>

        <div style={{ marginLeft: "auto" }}>
          <img
            src="/header_logos.png"
            alt="Logos institucionais"
            style={{ height: 44, width: "auto", display: "block" }}
          />
        </div>
      </header>

      {/* Etapas */}
      <div
        style={{
          marginTop: 12,
          display: "flex",
          justifyContent: "space-between",
          gap: 12,
          flexWrap: "wrap",
          alignItems: "center",
        }}
      >
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          {[
            { label: "1. Upload", done: !!file },
            { label: "2. Prévia", done: preview.length > 0 },
            { label: "3. Último licitado", done: preview.length > 0 },
            { label: "4. Ajuste manual", done: Object.keys(overrides).length > 0 },
            { label: "5. Gerar ZIP", done: false },
          ].map((s) => (
            <div
              key={s.label}
              style={{
                padding: "6px 10px",
                borderRadius: 999,
                border: "1px solid #e5e7eb",
                background: s.done ? "#ecfdf5" : "#f9fafb",
                color: s.done ? "#065f46" : "#374151",
                fontWeight: 700,
                fontSize: 12,
              }}
            >
              {s.label}
            </div>
          ))}
        </div>

        <div
          style={{
            display: "flex",
            gap: 10,
            alignItems: "center",
            flexWrap: "wrap",
            justifyContent: "flex-end",
            marginLeft: "auto",
          }}
        >
          <input
            id="pdfInput"
            type="file"
            accept="application/pdf"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            style={{ display: "none" }}
          />

          <label
            htmlFor="pdfInput"
            className={`btn ${focusChooseFile ? "btnCta" : "fileBtn"}`}
            title="Escolher PDF"
          >
            Escolher arquivo
          </label>

          <span style={{ fontSize: 12, color: "#4b5563", maxWidth: 360, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {file ? file.name : "Nenhum arquivo escolhido"}
          </span>

          <button
            onClick={loadPreview}
            disabled={!file || loadingPreview || previewReady}
            className={`btn ${focusPreview ? "btnCta" : previewReady ? "btnGhost" : "btnPrimary"}`}
            title={previewReady ? "Prévia já gerada (troque o arquivo para gerar novamente)" : "Gerar prévia"}
          >
            {loadingPreview ? "Carregando..." : "Gerar prévia"}
          </button>
        </div>
      </div>

{status && <p style={{ marginTop: 12 }}>{status}</p>}

      {preview.length > 0 && (
        <div style={{ marginTop: 16, overflowX: "hidden" }}>
          {/* Campos obrigatórios para o PDF comparativo */}
          <div
            style={{
              display: "flex",
              gap: 12,
              flexWrap: "wrap",
              alignItems: "flex-end",
              marginBottom: 12,
              padding: "10px 12px",
              border: "1px solid #ddd",
              borderRadius: 8,
              background: "#f0f0f0",
            }}
          >
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <label style={{ fontWeight: 700 }}>
                Número da Lista <span style={{ color: "#c62828" }}>*</span>
              </label>
              <input
                value={numeroLista}
                onChange={(e) => setNumeroLista(e.target.value)}
                placeholder="Ex: 123.26"
                style={{
                  width: 160,
                  fontSize: 14,
                  padding: "6px 8px",
                  border: `1px solid ${reqBorder(numeroLista)}` as any,
                  background: reqBg(numeroLista),
                  borderRadius: 6,
                }}
              />
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1, minWidth: 260 }}>
              <label style={{ fontWeight: 700 }}>
                Nome da Lista <span style={{ color: "#c62828" }}>*</span>
              </label>
              <input
                value={nomeLista}
                onChange={(e) => setNomeLista(e.target.value)}
                placeholder="Ex: Gerais Injetáveis"
                style={{
                  width: "100%",
                  fontSize: 14,
                  padding: "6px 8px",
                  border: `1px solid ${reqBorder(nomeLista)}` as any,
                  background: reqBg(nomeLista),
                  borderRadius: 6,
                }}
              />
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: 4, minWidth: 220 }}>
              <label style={{ fontWeight: 700 }}>
                Processo SEI <span style={{ color: "#c62828" }}>*</span>
              </label>
              <input
                value={processoSEI}
                onChange={(e) => setProcessoSEI(e.target.value)}
                placeholder="Ex: 23123.000000/2026-00"
                style={{
                  width: 220,
                  fontSize: 14,
                  padding: "6px 8px",
                  border: `1px solid ${reqBorder(processoSEI)}` as any,
                  background: reqBg(processoSEI),
                  borderRadius: 6,
                }}
              />
            </div>

            
            <div style={{ display: "flex", flexDirection: "column", gap: 4, minWidth: 220 }}>
              <label style={{ fontWeight: 700 }}>
                Responsável <span style={{ color: "#c62828" }}>*</span>
              </label>
              <input
                value={responsavel}
                onChange={(e) => setResponsavel(e.target.value)}
                placeholder="Ex: Pedro"
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
                  <td style={{ border: "1px solid #ddd", padding: "8px 8px", textAlign: "center" }}>
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
                        width: 88,
                        fontSize: 13,
                        padding: "3px 6px",
                        border: isActive ? "2px solid #1976d2" : "1px solid #ccc",
                        borderRadius: 6,
                        outline: "none",
                        background: isActive ? "#ffffff" : undefined,
                      }}
                    />
                  </td>
                  <td style={{ border: "1px solid #ddd", padding: "8px 8px", textAlign: "center" }}>{r.modo}</td>
                  <td style={{ border: "1px solid #ddd", padding: "8px 8px", textAlign: "center" }}>
                    {fmtBRL(r.valorFinal)}
                  </td>
                  <td style={{ border: "1px solid #ddd", padding: "8px 8px", textAlign: "center" }}>
                    {fmtBRL(r.diffAbs)}
                  </td>
                  <td style={{ border: "1px solid #ddd", padding: "8px 8px", textAlign: "center" }}>
                    <div style={{ display: "flex", gap: 8 }}>
                      <button
                        onClick={() => openManualModal(r)}
                        disabled={!r.allowManual}
                        title={
                          r.allowManual
                            ? r.hasOverride
                              ? "Ajuste manual já salvo (clique para revisar)"
                              : "Ajustar manualmente"
                            : "Só disponível quando Valor calculado ≤ 1,2× Último licitado"
                        }
                        className="btn"
                        style={(() => {
                          if (!r.allowManual) {
                            return {
                              cursor: "not-allowed",
                              opacity: 0.6,
                            };
                          }
                          if (r.adjustColor === "green") {
                            return {
                              background: "#2e7d32",
                              color: "white",
                              border: "1px solid #1b5e20",
                              fontWeight: 700,
                              cursor: "pointer",
                            };
                          }
                          if (r.adjustColor === "red") {
                            return {
                              background: "#c62828",
                              color: "white",
                              border: "1px solid #8e0000",
                              fontWeight: 700,
                              cursor: "pointer",
                            };
                          }
                          // yellow
                          return {
                            background: "#f1c232",
                            color: "#000",
                            border: "1px solid #c9a100",
                            fontWeight: 700,
                            cursor: "pointer",
                          };
                        })()}
                      >
                        Ajustar
                      </button>
                      {r.hasOverride && (
                        <button className="btn btnGhost" onClick={() => clearManualOverride(r.item)}>
                          Limpar
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

      {/* Modal */}
      {modalItem && (
        <div
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0,0,0,0.45)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            padding: 16,
            zIndex: 50,
          }}
          onClick={(e) => {
            if (e.target === e.currentTarget) closeModal();
          }}
        >
          <div
            style={{
              width: "min(900px, 100%)",
              background: "white",
              borderRadius: 8,
              padding: 16,
              maxHeight: "85vh",
              overflowY: "auto",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
              <div>
                <h2 style={{ margin: 0 }}>Ajuste manual — {modalItem.item}</h2>
                <p style={{ margin: "6px 0 0" }}>
                  Selecione os valores que devem compor o cálculo. Os indicadores (média/mediana/CV)
                  são recalculados em tempo real.
                </p>
              </div>
              <button onClick={closeModal}>Fechar</button>
            </div>

            <div style={{ marginTop: 12, display: "flex", gap: 16, flexWrap: "wrap" }}>
              <div style={{ flex: 1, minWidth: 320 }}>
                <h3 style={{ margin: "10px 0" }}>Valores brutos</h3>
                <div style={{ border: "1px solid #eee", borderRadius: 6 }}>
                  {modalSortedEntries.map((e, rowIdx) => (
                    <label
                      key={e.idx}
                      style={{
                        display: "flex",
                        gap: 10,
                        alignItems: "center",
                        padding: "8px 10px",
                        borderBottom:
                          rowIdx === modalSortedEntries.length - 1 ? "none" : "1px solid #eee",
                        background: modalAutoExclAltos.has(e.idx)
                          ? "#fdecea"
                          : modalAutoExclBaixos.has(e.idx)
                          ? "#fff7cc"
                          : undefined,
                        cursor: "pointer",
                      }}
                    >
                      <input
                        type="checkbox"
                        checked={modalSelected.includes(e.idx)}
                        onChange={() => toggleModalIndex(e.idx)}
                      />
                      <span style={{ width: 60, opacity: 0.7 }}>[{rowIdx + 1}]</span>
                      <span style={{ width: 110, fontFamily: "monospace" }}>{fmtSmart(e.valor)}</span>
                      <span style={{ flex: 1, opacity: 0.85 }}>Fonte: {e.fonte || "-"}</span>
                    </label>
                  ))}
                </div>
              </div>

              <div style={{ flex: 1, minWidth: 320 }}>
                <h3 style={{ margin: "10px 0" }}>Cálculo (dinâmico)</h3>

                <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
                  <div>
                    <div style={{ fontWeight: 700 }}>Qtd. incluída</div>
                    <div>{modalSelected.length}</div>
                  </div>
                  <div>
                    <div style={{ fontWeight: 700 }}>Qtd. excluída</div>
                    <div>{Math.max(0, modalItem.valores_brutos.length - modalSelected.length)}</div>
                  </div>
                  <div>
                    <div style={{ fontWeight: 700 }}>Média</div>
                    <div style={{ fontFamily: "monospace" }}>{fmtSmart(modalStats.mean)}</div>
                  </div>
                  <div>
                    <div style={{ fontWeight: 700 }}>Mediana</div>
                    <div style={{ fontFamily: "monospace" }}>{fmtSmart(modalStats.median)}</div>
                  </div>
                  <div>
                    <div style={{ fontWeight: 700 }}>CV</div>
                    <div>{pct2(modalStats.cv)}</div>
                  </div>
                </div>

                <div style={{ marginTop: 12 }}>
                  <div style={{ fontWeight: 700 }}>Método final</div>
                  <div style={{ display: "flex", gap: 12, marginTop: 6 }}>
                    <label style={{ display: "flex", gap: 8, alignItems: "center" }}>
                      <input
                        type="radio"
                        name="method"
                        checked={modalMethod === "media"}
                        onChange={() => setModalMethod("media")}
                      />
                      Média
                    </label>
                    <label style={{ display: "flex", gap: 8, alignItems: "center" }}>
                      <input
                        type="radio"
                        name="method"
                        checked={modalMethod === "mediana"}
                        onChange={() => setModalMethod("mediana")}
                      />
                      Mediana
                    </label>
                    <span style={{ opacity: 0.7 }}>
                      Sugestão pelo CV: <strong>{modalStats.suggested === "media" ? "Média" : "Mediana"}</strong>
                    </span>
                  </div>
                </div>

                <div style={{ marginTop: 12 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 16 }}>
                    <div style={{ flex: 1 }}>
                      <div style={{ fontWeight: 700 }}>Valor estimado</div>
                      <div style={{ fontSize: 18, fontWeight: 800, marginTop: 4 }}>
                        {fmtSmart(modalStats.finalVal)}
                      </div>
                    </div>
                    <div style={{ textAlign: "right" }}>
                      <div style={{ fontWeight: 700 }}>Último valor cotado</div>
                      <div style={{ fontSize: 18, fontWeight: 800, marginTop: 4 }}>
                        {fmtSmart(parseBRL(lastQuotes[modalItem.item] || "") || null)}
                      </div>
                    </div>
                  </div>
                </div>

                <div style={{ marginTop: 16 }}>
                  <div style={{ fontWeight: 700 }}>Justificativa</div>
                  <select
                    value={modalJustCode}
                    onChange={(e) => {
                      const code = e.target.value;
                      setModalJustCode(code);
                      if (!code) {
                        setModalJustText("");
                      } else if (code === "OUTRO") {
                        setModalJustText("");
                      } else {
                        setModalJustText(JUST_OPTIONS[code] || "");
                      }
                    }}
                    style={{ marginTop: 6, width: "100%" }}
                  >
                    <option value="">(opcional) Selecione um motivo</option>
                    <option value="PADRAO_1">Exclusão de cotações abaixo do último preço homologado</option>
                    <option value="PADRAO_2">Exclusão (abaixo do último preço) por exclusividade de fornecedor</option>
                    <option value="OUTRO">Outro</option>
                  </select>
                  <textarea
                    value={modalJustText}
                    onChange={(e) => setModalJustText(e.target.value)}
                    placeholder="(opcional) Descreva a justificativa"
                    disabled={!!modalJustCode && modalJustCode !== "OUTRO"}
                    style={{ marginTop: 8, width: "100%", minHeight: 90 }}
                  />
                </div>

                <div style={{ display: "flex", gap: 12, marginTop: 16, justifyContent: "flex-end" }}>
                  <button className="btn btnGhost" onClick={closeModal}>
                    Cancelar
                  </button>
                  <button className="btn btnPrimary" onClick={saveManualOverride}>
                    Salvar ajuste
                  </button>
                </div>
              </div>
            </div>
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
          padding: "10px 16px",
          backdropFilter: "blur(6px)",
        }}
      >
        <div
          style={{
            maxWidth: 1200,
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
              disabled={!file || !preview.length || loadingGenerate || !numeroLista.trim() || !nomeLista.trim() || !processoSEI.trim() || !responsavel.trim()}
              className={`btn ${canGenerate ? "btnCta" : "btnPrimary"}`}
              title={!preview.length ? "Gere a prévia primeiro" : canGenerate ? "Baixar ZIP (PDFs)" : "Preencha os campos obrigatórios"}
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
