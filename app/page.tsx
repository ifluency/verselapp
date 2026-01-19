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
  valores_brutos: number[];
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
  return n.toFixed(2).replace(".", ",");
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

export default function Page() {
  const [file, setFile] = useState<File | null>(null);
  const [status, setStatus] = useState<string>("");

  const [preview, setPreview] = useState<PreviewItem[]>([]);
  const [loadingPreview, setLoadingPreview] = useState(false);
  const [loadingGenerate, setLoadingGenerate] = useState(false);

  const [lastQuotes, setLastQuotes] = useState<Record<string, string>>({});
  const [overrides, setOverrides] = useState<Record<string, ManualOverride>>({});

  const [modalItemId, setModalItemId] = useState<string | null>(null);
  const modalItem = useMemo(
    () => preview.find((p) => p.item === modalItemId) || null,
    [preview, modalItemId]
  );

  const [modalSelected, setModalSelected] = useState<number[]>([]);
  const [modalMethod, setModalMethod] = useState<"media" | "mediana">("media");
  const [modalJustCode, setModalJustCode] = useState<string>("");
  const [modalJustText, setModalJustText] = useState<string>("");

  const modalIncludedValues = useMemo(() => {
    if (!modalItem) return [];
    const vals: number[] = [];
    for (const idx of modalSelected) {
      const v = modalItem.valores_brutos[idx];
      if (typeof v === "number" && Number.isFinite(v)) vals.push(v);
    }
    return vals;
  }, [modalItem, modalSelected]);

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
    const last = parseBRL(lastQuotes[item.item] || "");
    const calc = item.valor_calculado;
    const allowed = last !== null && calc !== null && calc < last;
    if (!allowed) return;

    setModalItemId(item.item);

    const allIdx = item.valores_brutos.map((_, i) => i);
    setModalSelected(allIdx);

    const existing = overrides[item.item];
    if (existing) {
      setModalSelected(existing.includedIndices);
      setModalMethod(existing.method);
      setModalJustCode(existing.justificativaCodigo);
      setModalJustText(existing.justificativaTexto);
    } else {
      const baseCv = cv(item.valores_brutos);
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
    setOverrides((prev) => ({
      ...prev,
      [modalItem.item]: {
        includedIndices: [...modalSelected].sort((a, b) => a - b),
        method: modalMethod,
        justificativaCodigo: modalJustCode,
        justificativaTexto: modalJustText,
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

    setStatus("Gerando arquivos finais...");
    setLoadingGenerate(true);

    const last_quotes: Record<string, number> = {};
    for (const it of preview) {
      const v = parseBRL(lastQuotes[it.item] || "");
      if (v !== null) last_quotes[it.item] = v;
    }

    const manual_overrides: any = {};
    for (const [itemId, ov] of Object.entries(overrides)) {
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
      const allowManual = last !== null && calc !== null && calc < last;
      const ov = overrides[it.item];

      let modo = "Auto";
      let metodo = "";
      let valorFinal: number | null = calc;

      if (allowManual && ov) {
        modo = "Manual";
        metodo = ov.method === "media" ? "Média" : "Mediana";
        const included = ov.includedIndices
          .map((idx) => it.valores_brutos[idx])
          .filter((v) => typeof v === "number" && Number.isFinite(v)) as number[];
        valorFinal = ov.method === "media" ? mean(included) : median(included);
      } else if (allowManual && !ov) {
        modo = "Pendente";
      }

      const diffAbs = last !== null && valorFinal !== null ? valorFinal - last : null;
      const diffPct = last !== null && last !== 0 && valorFinal !== null ? (diffAbs! / last) * 100 : null;

      return {
        ...it,
        last,
        allowManual,
        modo,
        metodo,
        valorFinal,
        diffAbs,
        diffPct,
        hasOverride: !!ov,
      };
    });
  }, [preview, lastQuotes, overrides]);

  return (
    <main style={{ maxWidth: 1200, margin: "32px auto", padding: "0 16px" }}>
      <h1>UPDE — Preços de Referência (Prévia + Ajuste Manual)</h1>
      <p>
        1) Faça upload do PDF do ComprasGOV → 2) Veja a prévia → 3) Informe o último licitado → 4)
        Ajuste manual (somente quando <strong>Valor calculado &lt; Último licitado</strong>) → 5)
        Gere o ZIP.
      </p>

      <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
        <input type="file" accept="application/pdf" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />

        <button onClick={loadPreview} disabled={!file || loadingPreview}>
          {loadingPreview ? "Carregando..." : "Gerar prévia"}
        </button>

        <button onClick={generateZip} disabled={!file || !preview.length || loadingGenerate} style={{ fontWeight: 700 }}>
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
        <div style={{ marginTop: 20, overflowX: "auto" }}>
          <table style={{ borderCollapse: "collapse", width: "100%" }}>
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
                  "Dif. (%)",
                  "Ajuste",
                ].map((h) => (
                  <th
                    key={h}
                    style={{
                      border: "1px solid #ddd",
                      padding: "8px 10px",
                      background: "#f7f7f7",
                      textAlign: "left",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {tableRows.map((r) => (
                <tr key={r.item}>
                  <td style={{ border: "1px solid #ddd", padding: "8px 10px" }}>{r.item}</td>
                  <td style={{ border: "1px solid #ddd", padding: "8px 10px" }}>{r.catmat}</td>
                  <td style={{ border: "1px solid #ddd", padding: "8px 10px" }}>{r.n_bruto}</td>
                  <td style={{ border: "1px solid #ddd", padding: "8px 10px" }}>{r.n_final}</td>
                  <td style={{ border: "1px solid #ddd", padding: "8px 10px" }}>{r.excl_altos}</td>
                  <td style={{ border: "1px solid #ddd", padding: "8px 10px" }}>{r.excl_baixos}</td>
                  <td style={{ border: "1px solid #ddd", padding: "8px 10px", whiteSpace: "nowrap" }}>{fmtBRL(r.valor_calculado)}</td>

                  <td style={{ border: "1px solid #ddd", padding: "8px 10px", whiteSpace: "nowrap" }}>
                    <input
                      value={lastQuotes[r.item] || ""}
                      onChange={(e) => setLastQuotes((prev) => ({ ...prev, [r.item]: e.target.value }))}
                      placeholder="ex: 1.234,56"
                      style={{ width: 120 }}
                    />
                  </td>

                  <td style={{ border: "1px solid #ddd", padding: "8px 10px" }}>{r.modo}</td>
                  <td style={{ border: "1px solid #ddd", padding: "8px 10px", whiteSpace: "nowrap" }}>{fmtBRL(r.valorFinal)}</td>
                  <td style={{ border: "1px solid #ddd", padding: "8px 10px", whiteSpace: "nowrap" }}>{fmtBRL(r.diffAbs)}</td>
                  <td style={{ border: "1px solid #ddd", padding: "8px 10px", whiteSpace: "nowrap" }}>
                    {r.diffPct === null ? "" : r.diffPct.toFixed(2).replace(".", ",") + "%"}
                  </td>

                  <td style={{ border: "1px solid #ddd", padding: "8px 10px", whiteSpace: "nowrap" }}>
                    <div style={{ display: "flex", gap: 8 }}>
                      <button
                        onClick={() => openManualModal(r)}
                        disabled={!r.allowManual}
                        title={r.allowManual ? "Ajustar manualmente" : "Só disponível quando Valor calculado < Último licitado"}
                      >
                        Ajustar
                      </button>
                      {r.hasOverride && <button onClick={() => clearManualOverride(r.item)}>Limpar</button>}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

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
                  Selecione os valores que devem compor o cálculo. Os indicadores (média/mediana/CV) são recalculados em tempo real.
                </p>
              </div>
              <button onClick={closeModal}>Fechar</button>
            </div>

            <div style={{ marginTop: 12, display: "flex", gap: 16, flexWrap: "wrap" }}>
              <div style={{ flex: 1, minWidth: 320 }}>
                <h3 style={{ margin: "10px 0" }}>Valores brutos</h3>
                <div style={{ border: "1px solid #eee", borderRadius: 6 }}>
                  {modalItem.valores_brutos.map((v, idx) => (
                    <label
                      key={idx}
                      style={{
                        display: "flex",
                        gap: 10,
                        alignItems: "center",
                        padding: "8px 10px",
                        borderBottom: idx === modalItem.valores_brutos.length - 1 ? "none" : "1px solid #eee",
                        cursor: "pointer",
                      }}
                    >
                      <input type="checkbox" checked={modalSelected.includes(idx)} onChange={() => toggleModalIndex(idx)} />
                      <span style={{ width: 56, opacity: 0.7 }}>[{idx}]</span>
                      <span style={{ fontFamily: "monospace" }}>{v.toFixed(4)}</span>
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
                    <div style={{ fontFamily: "monospace" }}>{modalStats.mean === null ? "" : modalStats.mean.toFixed(4)}</div>
                  </div>
                  <div>
                    <div style={{ fontWeight: 700 }}>Mediana</div>
                    <div style={{ fontFamily: "monospace" }}>{modalStats.median === null ? "" : modalStats.median.toFixed(4)}</div>
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
                      <input type="radio" name="method" checked={modalMethod === "media"} onChange={() => setModalMethod("media")} />
                      Média
                    </label>
                    <label style={{ display: "flex", gap: 8, alignItems: "center" }}>
                      <input type="radio" name="method" checked={modalMethod === "mediana"} onChange={() => setModalMethod("mediana")} />
                      Mediana
                    </label>
                    <span style={{ opacity: 0.7 }}>
                      Sugestão pelo CV: <strong>{modalStats.suggested === "media" ? "Média" : "Mediana"}</strong>
                    </span>
                  </div>
                </div>

                <div style={{ marginTop: 12 }}>
                  <div style={{ fontWeight: 700 }}>Valor final (manual)</div>
                  <div style={{ fontSize: 18, fontWeight: 800, marginTop: 4 }}>{fmtBRL(modalStats.finalVal)}</div>
                </div>

                <div style={{ marginTop: 16 }}>
                  <div style={{ fontWeight: 700 }}>Justificativa</div>
                  <select value={modalJustCode} onChange={(e) => setModalJustCode(e.target.value)} style={{ marginTop: 6, width: "100%" }}>
                    <option value="">(opcional) Selecione um motivo</option>
                    <option value="OUTLIERS_MANUAL">Exclusão manual de valores destoantes</option>
                    <option value="DADOS_INCONSISTENTES">Inconsistência/indícios de erro nos dados</option>
                    <option value="HISTORICO_INSTITUICAO">Adequação ao histórico da instituição</option>
                    <option value="OUTRO">Outro</option>
                  </select>
                  <textarea
                    value={modalJustText}
                    onChange={(e) => setModalJustText(e.target.value)}
                    placeholder="(opcional) Descreva a justificativa"
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
