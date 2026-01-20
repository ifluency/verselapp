"use client";

import React, { useMemo, useState } from "react";

type PreviewItem = {
  item: string;
  catmat: string;
  n_bruto: number;
  n_final: number;
  excl_altos: number;
  excl_baixos: number;
  valor_calculado: number | null;
  valores_brutos: { idx: number; valor: number; fonte: string }[];
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
  OUTLIERS_MANUAL: "Exclusão manual de valores destoantes (outliers) a partir da análise dos valores brutos.",
  FONTE_PRIORIZADA: "Priorização de fontes mais confiáveis (ex.: compras anteriores/homologações) frente a cotações menos consistentes.",
  MERCADO_OSCILACAO: "Oscilação de mercado identificada; adotado critério mais aderente ao contexto recente de aquisição.",
  OUTRO: "",
};

export default function Page() {
  const [file, setFile] = useState<File | null>(null);
  const [status, setStatus] = useState<string>("");

  // Campos obrigatórios para o PDF Tabela Comparativa de Valores
  const [numeroLista, setNumeroLista] = useState<string>("");
  const [nomeLista, setNomeLista] = useState<string>("");
  const [processoSEI, setProcessoSEI] = useState<string>("");

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

    // Inicializa com tudo selecionado
    const allIdx = item.valores_brutos.map((e) => e.idx);
    setModalSelected(allIdx);

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
    setPreview([]);
    setOverrides({});
    setLastQuotes({});
    setNumeroLista("");
    setNomeLista("");
    setProcessoSEI("");

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

    if (!numeroLista.trim() || !nomeLista.trim() || !processoSEI.trim()) {
      setStatus("Preencha os campos obrigatórios: Número da Lista, Nome da Lista e Processo SEI.");
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
      a.download = "resultado.zip";
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);

      setStatus("Concluído! ZIP gerado com Excel + 2 PDFs.");
    } catch (e: any) {
      setStatus(`Falha ao gerar ZIP: ${String(e)}`);
    } finally {
      setLoadingGenerate(false);
    }
  }

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

      let modo = "Auto";
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
    <main style={{ maxWidth: "100%", margin: "12px auto", padding: "0 8px" }}>
      <h1>UPDE — Preços de Referência (Prévia + Ajuste Manual)</h1>
      <p>
        1) Faça upload do PDF do ComprasGOV → 2) Veja a prévia → 3) Informe o último licitado → 4)
        Ajuste manual (liberado quando <strong>Valor calculado ≤ 1,2× Último licitado</strong>) → 5)
        Gere o ZIP.
      </p>

      <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
        <input
          type="file"
          accept="application/pdf"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
        />

        <button onClick={loadPreview} disabled={!file || loadingPreview}>
          {loadingPreview ? "Carregando..." : "Gerar prévia"}
        </button>

        <button
          onClick={generateZip}
          disabled={
            !file ||
            !preview.length ||
            loadingGenerate ||
            !numeroLista.trim() ||
            !nomeLista.trim() ||
            !processoSEI.trim()
          }
          style={{ fontWeight: 700 }}
        >
          {loadingGenerate ? "Gerando..." : "Gerar ZIP (Excel + PDFs)"}
        </button>

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
        >
          Debug (TXT)
        </button>
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
              background: "#fafafa",
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
                  border: "1px solid #ccc",
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
                  border: "1px solid #ccc",
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
                  border: "1px solid #ccc",
                  borderRadius: 6,
                }}
              />
            </div>

            {(!numeroLista.trim() || !nomeLista.trim() || !processoSEI.trim()) && (
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
                      textAlign: "left",
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
                  <td style={{ border: "1px solid #ddd", padding: "8px 8px" }}>{r.item}</td>
                  <td style={{ border: "1px solid #ddd", padding: "8px 8px" }}>{r.catmat}</td>
                  <td style={{ border: "1px solid #ddd", padding: "8px 8px" }}>{r.n_bruto}</td>
                  <td style={{ border: "1px solid #ddd", padding: "8px 8px" }}>{r.n_final}</td>
                  <td style={{ border: "1px solid #ddd", padding: "8px 8px" }}>{r.excl_altos}</td>
                  <td style={{ border: "1px solid #ddd", padding: "8px 8px" }}>{r.excl_baixos}</td>
                  <td style={{ border: "1px solid #ddd", padding: "8px 8px" }}>
                    {fmtBRL(r.valor_calculado)}
                  </td>
                  <td style={{ border: "1px solid #ddd", padding: "8px 8px" }}>
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
                  <td style={{ border: "1px solid #ddd", padding: "8px 8px" }}>{r.modo}</td>
                  <td style={{ border: "1px solid #ddd", padding: "8px 8px" }}>
                    {fmtBRL(r.valorFinal)}
                  </td>
                  <td style={{ border: "1px solid #ddd", padding: "8px 8px" }}>
                    {fmtBRL(r.diffAbs)}
                  </td>
                  <td style={{ border: "1px solid #ddd", padding: "8px 8px" }}>
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
                        style={(() => {
                          if (!r.allowManual) {
                            return {
                              padding: "4px 10px",
                              borderRadius: 6,
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
                              padding: "4px 10px",
                              borderRadius: 6,
                              cursor: "pointer",
                            };
                          }
                          if (r.adjustColor === "red") {
                            return {
                              background: "#c62828",
                              color: "white",
                              border: "1px solid #8e0000",
                              fontWeight: 700,
                              padding: "4px 10px",
                              borderRadius: 6,
                              cursor: "pointer",
                            };
                          }
                          // yellow
                          return {
                            background: "#f1c232",
                            color: "#000",
                            border: "1px solid #c9a100",
                            fontWeight: 700,
                            padding: "4px 10px",
                            borderRadius: 6,
                            cursor: "pointer",
                          };
                        })()}
                      >
                        Ajustar
                      </button>
                      {r.hasOverride && (
                        <button onClick={() => clearManualOverride(r.item)}>
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
                  <div style={{ fontWeight: 700 }}>Valor final (manual)</div>
                  <div style={{ fontSize: 18, fontWeight: 800, marginTop: 4 }}>
                    {fmtSmart(modalStats.finalVal)}
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
                    <option value="OUTLIERS_MANUAL">Exclusão manual de valores destoantes</option>
                    <option value="FONTE_PRIORIZADA">Priorização de fontes mais confiáveis</option>
                    <option value="MERCADO_OSCILACAO">Oscilação de mercado</option>
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
                  <button onClick={closeModal}>Cancelar</button>
                  <button onClick={saveManualOverride} style={{ fontWeight: 800 }}>
                    Salvar ajuste
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </main>
  );
}
