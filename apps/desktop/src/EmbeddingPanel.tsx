import { displayError, errorText, type DisplayError } from "./displayError";
import { t as translateMessage } from "./localization";
import { DocumentFilePicker } from "./DocumentFilePicker";
import { t } from "./localization";
import { useEffect, useRef, useState } from "react";
type Result = {
  model: string;
  dimensions: number;
  input_tokens: number | null;
  device?: string;
  fallback_reason?: string;
  vector?: number[];
};
type RebuildResult = {
  cancelled?: boolean;
  rebuilt: { document_id: string; model: string; dimensions: number }[];
  failed: { document_id: string; error: DisplayError }[];
};
type InstalledEmbedding = { kind: string; repo_id: string; revision: string };
type EmbeddingSettings = {
  backend: string;
  chunk_size?: number;
  chunk_overlap?: number;
  selected_model?: { repo_id: string; revision: string };
};
type Props = {
  run: <T>(method: string, params?: unknown) => Promise<T | undefined>;
};
export function EmbeddingPanel({ run }: Props) {
  const [text, setText] = useState(""),
    [path, setPath] = useState(""),
    [fileName, setFileName] = useState(""),
    [result, setResult] = useState<Result>(),
    [similarity, setSimilarity] = useState<number>(),
    [busy, setBusy] = useState(false);
  const rebuildRunning = useRef(false);
  const rebuildId = useRef<string | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [rebuilding, setRebuilding] = useState(false);
  const [confirmRebuild, setConfirmRebuild] = useState(false);
  const [rebuildResult, setRebuildResult] = useState<RebuildResult>();
  const [rebuildError, setRebuildError] = useState<DisplayError>("");
  const [backend, setBackend] = useState("auto");
  const [loaded, setLoaded] = useState(false);
  const [chunkSize, setChunkSize] = useState("1500");
  const [chunkOverlap, setChunkOverlap] = useState("200");
  const [chunkStatus, setChunkStatus] = useState("");
  const [chunkError, setChunkError] = useState<DisplayError>("");
  const [installedModels, setInstalledModels] = useState<InstalledEmbedding[]>(
    [],
  );
  const [selectedModel, setSelectedModel] =
    useState<EmbeddingSettings["selected_model"]>();
  const previous = useRef<{ model: string; vector: number[] }>({
    model: "",
    vector: [],
  });
  useEffect(() => {
    let alive = true;
    void run<InstalledEmbedding[]>("services.read", { name: "models.installed", args: {} }).then((data) => {
      if (alive && data)
        setInstalledModels(data.filter((model) => model.kind === "embedding"));
    });
    void run<EmbeddingSettings>("embedding.settings").then((data) => {
      if (alive && data) {
        setBackend(data.backend);
        setChunkSize(String(data.chunk_size ?? 1500));
        setChunkOverlap(String(data.chunk_overlap ?? 200));
        setSelectedModel(data.selected_model);
        setLoaded(true);
      }
    });
    return () => {
      alive = false;
      if (rebuildId.current) void run("embedding.cancel", {request_id: rebuildId.current}).catch(() => undefined);
    };
  }, []);
  const saveChunking = async () => {
    setChunkError("");
    const size = Number(chunkSize), overlap = Number(chunkOverlap);
    if (!chunkSize.trim() || !chunkOverlap.trim() || !Number.isInteger(size) || size < 256 || size > 8192 || !Number.isInteger(overlap) || overlap < 0 || overlap >= size) {
      setChunkStatus("Enter a chunk size from 256 to 8192 and an overlap smaller than the size.");
      return;
    }
    setBusy(true);
    setChunkStatus("");
    try {
      const saved = await run<EmbeddingSettings>("embedding.settings", {chunk_size: size, chunk_overlap: overlap});
      if (saved) setChunkStatus("Chunk settings saved. Rebuild existing indexes to apply them.");
    } catch (error) { setChunkError(displayError(error)); }
    finally { setBusy(false); }
  };
  const choose = async (value: string) => {
    setBusy(true);
    try {
      const saved = await run<{ backend: string }>("embedding.settings", {
        backend: value,
      });
      if (saved) {
        setBackend(saved.backend);
        setResult(undefined);
        setSimilarity(undefined);
        previous.current = { model: "", vector: [] };
      }
    } finally {
      setBusy(false);
    }
  };
  const selectModel = async (value: string) => {
    setBusy(true);
    try {
      if (value === "default") {
        const saved = await run<EmbeddingSettings>("embedding.settings", {
          reset_model: true,
        });
        if (!saved) return;
        setSelectedModel(saved.selected_model);
      } else {
        const model = installedModels[Number(value)];
        if (!model) return;
        const activated = await run("models.activate", {
          kind: "embedding",
          repo_id: model.repo_id,
          revision: model.revision,
          mode: backend === "cpu" ? "cpu" : "vulkan",
        });
        if (!activated) return;
        const saved = await run<EmbeddingSettings>("embedding.settings");
        if (!saved) return;
        setBackend(saved.backend);
        setSelectedModel(saved.selected_model);
      }
      setResult(undefined);
      setSimilarity(undefined);
      previous.current = { model: "", vector: [] };
    } finally {
      setBusy(false);
    }
  };
  const rebuild = async () => {
    if (rebuildRunning.current) return;
    rebuildRunning.current = true;
    const request_id = crypto.randomUUID();
    rebuildId.current = request_id;
    setCancelling(false);
    setRebuilding(true);
    setConfirmRebuild(false);
    setBusy(true);
    setRebuildResult(undefined);
    setRebuildError("");
    try {
      const data = await run<RebuildResult>("documents.rebuild", { backend, request_id });
      if (data) setRebuildResult(data);
    } catch (error) {
      setRebuildError(displayError(error));
    } finally {
      rebuildId.current = null;
      setCancelling(false);
      rebuildRunning.current = false;
      setRebuilding(false);
      setBusy(false);
    }
  };
  const cancelRebuild = async () => {
    const request_id = rebuildId.current;
    if (!request_id || cancelling) return;
    setCancelling(true);
    try {
      const reply = await run("embedding.cancel", { request_id });
      if (!reply) setCancelling(false);
    } catch (error) {
      setRebuildError(displayError(error));
      setCancelling(false);
    }
  };
  const create = async () => {
    setBusy(true);
    try {
      const document =
        path.trim() && fileName && !text.trim()
          ? await run<{ id: string }>("documents.import", {
              path,
              name: fileName,
            })
          : undefined;
      if (path.trim() && fileName && !text.trim() && !document) return;
      const data = document
        ? await run<Result>("documents.embed", {
            identifier: document.id,
            backend,
          })
        : await run<Result>("api.embed", { text, path, backend });
      if (data) {
        setSimilarity(
          previous.current.model === data.model &&
            data.vector &&
            previous.current.vector.length === data.vector.length
            ? data.vector.reduce(
                (sum, v, i) => sum + v * previous.current.vector[i],
                0,
              )
            : undefined,
        );
        previous.current = { model: data.model, vector: data.vector ?? [] };
        setResult(data);
      }
    } finally {
      setBusy(false);
    }
  };
  return (
    <section
      className="api-usage settings-content document-panel"
      aria-label={t("Multimodal embeddings")}
    >
      <h3>{t("Embedding models and documents")}</h3>
      <DocumentFilePicker
        accept=".txt,.md,.pdf,.docx,.png,.jpg,.jpeg,.mp3,.wav,.mp4,.mov"
        disabled={busy}
        onSelect={(path, name) => {
          setPath(path);
          setFileName(name);
          setText("");
          setResult(undefined);
        }}
      />
      {fileName && <p role="status">{fileName}</p>}
      <label>
        {t("Computing embedding")}
        <select
          aria-label={t("Embedding mode")}
          value={backend}
          disabled={busy || !loaded}
          onChange={(e) => void choose(e.target.value)}
        >
          <option value="auto">
            {" "}
            {t("Google API → Local Vulkan GPU fallback (no CPU fallback)")}
          </option>
          <option value="cloud">{translateMessage("Google API — Gemini Embedding 2")}</option>
          <option value="local">
            {t("Local — Vulkan GPU required (no CPU fallback)")}
          </option>
          <option value="cpu"> {t("Local — CPU only")} </option>
        </select>
      </label>
      <label>
        {t("Local embedding model")}
        <select
          aria-label={t("Local embedding model")}
          disabled={busy || !loaded}
          value={
            selectedModel
              ? String(
                  installedModels.findIndex(
                    (model) =>
                      model.repo_id === selectedModel.repo_id &&
                      model.revision === selectedModel.revision,
                  ),
                )
              : "default"
          }
          onChange={(event) => void selectModel(event.target.value)}
        >
          <option value="default">
            {t("Built-in EmbeddingGemma 2 (download on first use)")}
          </option>
          {selectedModel &&
            !installedModels.some(
              (model) =>
                model.repo_id === selectedModel.repo_id &&
                model.revision === selectedModel.revision,
            ) && (
              <option value="-1" disabled>
                {selectedModel.repo_id} —{" "}
                {t("Unavailable; select another model")}
              </option>
            )}
          {installedModels.map((model, index) => (
            <option key={model.repo_id + model.revision} value={String(index)}>
              {model.repo_id} · {model.revision.slice(0, 12)}
            </option>
          ))}
        </select>
      </label>
      <fieldset disabled={busy || !loaded}>
        <legend>{t("Document chunks")}</legend>
        <label>{t("Chunk size (characters)")}<input type="number" min="256" max="8192" step="1" value={chunkSize} onChange={event => { setChunkSize(event.target.value); setChunkStatus(""); }} /></label>
        <label>{t("Chunk overlap (characters)")}<input type="number" min="0" max="8191" step="1" value={chunkOverlap} onChange={event => { setChunkOverlap(event.target.value); setChunkStatus(""); }} /></label>
        <button onClick={() => void saveChunking()}>{t("Save chunk settings")}</button>
        <p className="note">{t("Shared by uploaded files, chat and document tools. Changes apply to new indexing; rebuild existing indexes explicitly. Smaller chunks increase embedding calls.")}</p>
        {chunkStatus && <p role="status">{t(chunkStatus)}</p>}
        {chunkError && <p role="alert">{errorText(chunkError)}</p>}
      </fieldset>
      <p className="note">
        {t(
          "Download embedding models in Settings → Models and providers → Model Library. Selection is shared with chat, documents and tools; weights load only when used. Changing models requires rebuilding existing indexes. Model capabilities determine which file types can be embedded.",
        )}
      </p>
      <p className="note">
        {backend === "cloud" || backend === "auto"
          ? t(
              "Google API selected: material will be sent to Google after clicking the button. Uses shared Gemini key. In automatic mode, local fallback is used on quota/unavailability.",
            )
          : t(
              "Material is processed on this PC. The selected model and runtime load on demand. The built-in default downloads about 900 MB on first use; installed models reuse their downloaded weights. Supported file types depend on the selected model. PDF scans use local OCR. First computation may take longer.",
            )}
      </p>
      <p className="note">
        {t(
          "Converts text, image, audio, video, or PDF into a vector for comparison/search. This is not a chat model response. Selected uploaded files and their embeddings are stored in the shared local catalogue. Chat and tools can read and search them. Text-only comparisons stay in this window.",
        )}
      </p>
      <div data-document-rebuild aria-busy={rebuilding}>
        {rebuilding && <>
          <p role="status">{t(cancelling ? "Cancelling index rebuild…" : "Rebuilding document indexes…")}</p>
          <button disabled={cancelling} onClick={() => void cancelRebuild()}>{t("Cancel rebuild")}</button>
        </>}
        <p className="note">
          {t(
            "Rebuild all previously indexed documents with the selected embedding provider and model. Failed documents keep their previous index. Auto/Cloud may send their contents to Google. This can take time.",
          )}
        </p>
        {!confirmRebuild ? (
          <button
            disabled={!loaded || busy}
            onClick={() => setConfirmRebuild(true)}
          >
            {t("Rebuild document indexes")}
          </button>
        ) : (
          <div role="group" aria-label={t("Confirm index rebuild")}>
            <p>
              {t("Rebuild all indexed documents using the current selection?")}
            </p>
            <button disabled={busy} onClick={() => void rebuild()}>
              {t("Confirm index rebuild")}
            </button>
            <button disabled={busy} onClick={() => setConfirmRebuild(false)}>
              {t("Cancel")}
            </button>
          </div>
        )}
        {rebuildResult?.cancelled && <p role="status">{t("Index rebuild cancelled. Completed documents are kept; interrupted documents retain their previous index.")}</p>}
        {rebuildResult && (
          <div role={rebuildResult.failed.length ? "alert" : "status"}>
            <p>
              {t(
                "Indexes rebuilt: {0}. Failed: {1}.",
                rebuildResult.rebuilt.length,
                rebuildResult.failed.length,
              )}
            </p>
            {rebuildResult.failed.map((item) => (
              <p key={item.document_id}>
                {item.document_id}: {errorText(item.error)}
              </p>
            ))}
          </div>
        )}
        {rebuildError && <p role="alert">{errorText(rebuildError)}</p>}
      </div>
      <label>
        {t("Text for embedding")}
        <textarea
          aria-label={t("Text for embedding")}
          maxLength={12000}
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            if (e.target.value.trim()) {
              setPath("");
              setFileName("");
            }
          }}
        />
      </label>
      <details>
        <summary>{t("Advanced: file path")}</summary>
        <label>
          {" "}
          {t("Path to a single file (optional)")}
          <input
            aria-label={t("Path to embedding file")}
            maxLength={2048}
            value={path}
            onChange={(e) => {
              setPath(e.target.value);
              setFileName("");
              if (e.target.value.trim()) setText("");
            }}
            placeholder={t("Choose a file or enter its path")}
          />
        </label>
      </details>
      <p className="note">
        {t(
          "PNG/JPEG; MP3/WAV up to 180s; MP4/MOV up to 120s; PDF up to 6 pages. In Jarvis — one file up to 12 MiB. Maximum 8192 tokens; cloud input is checked via countTokens, local — via llama.cpp. Video audio track is not indexed separately.",
        )}
      </p>
      <button
        disabled={!loaded || busy || (!text.trim() && !path.trim())}
        onClick={create}
      >
        {busy
          ? t("Computing...")
          : t(
              path.trim() && !text.trim()
                ? "Index file for chat and tools"
                : "Create embedding / Compare with previous",
            )}
      </button>
      {result && (
        <p role="status">
          {result.model}: {result.dimensions}
          {t("dimensions,")} {result.input_tokens ?? "—"} {t("input tokens.")}{" "}
          {result.device &&
            t("Device: {0}.", result.device === "none" ? "CPU" : result.device)}
          {result.fallback_reason &&
            " Local fallback used: " + result.fallback_reason}
          {similarity !== undefined && (
            <>
              {" "}
              {t("Cosine similarity with previous material:")}{" "}
              {similarity.toFixed(4)}.
            </>
          )}
        </p>
      )}
    </section>
  );
}
