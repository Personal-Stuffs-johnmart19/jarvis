import { useLocale } from "../../../../src/i18n";
import { displayError, errorText, type DisplayError } from "../../../../src/displayError";
import { invokeOwned } from "@/host/core";
import { listen } from "@/host/event";
import { confirmAction } from "../../../../src/confirmAction";
import { t as translateMessage } from "../../../../src/localization";
import { useState, useEffect, useRef } from "react";
import { t } from "../../../../src/localization";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import {
  inspectTargetDisk,
  memoryGuidance,
  type DiskPreflight,
  type ModelKind,
  type RepoInfo,
  type ModelManifest,
  type ModelVariantSummary,
  fetchRepoInfo,
  matchBundles,
  visionProjectorFiles,
  listInstalledModels,
  deleteInstalledModel,
  cancelModelDownload,
  downloadModelBundle,
  activateModel,
  summarizeBundleMatches,
  formatBytes,
  calculateProgress,
} from "../lib/modelLibrary";

type ActivationFeedback =
  | { template: string; values?: (string | number)[] }
  | { error: DisplayError };
const feedbackDeps = { invoke: invokeOwned, listen };

export function ModelLibrary() {
  const locale = useLocale();
  const [repoId, setRepoId] = useState("");
  const [diskRefresh,setDiskRefresh] = useState(0);
  const [diskResult,setDiskResult] = useState<{key:string; value?:DiskPreflight; error?:DisplayError}>();
  const [revision, setRevision] = useState("");
  const requestedKind = new URLSearchParams(window.location.search).get("libraryKind");
  const initialKind = requestedKind === "tts" ? "tts" : "llm";
  const [kind, setKind] = useState<ModelKind>(initialKind);
  const [expanded, setExpanded] = useState(initialKind === "tts");
  const libraryRef = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    const open = (event: Event) => {
      if ((event as CustomEvent).detail !== "tts") return;
      setKind("tts"); setExpanded(true);
    };
    window.addEventListener("jarvis:model-library", open);
    return () => window.removeEventListener("jarvis:model-library", open);
  }, []);
  useEffect(() => {
    if (initialKind !== "tts") return;
    const frame = requestAnimationFrame(() => {
      libraryRef.current?.scrollIntoView({block:"start"});
      libraryRef.current?.focus({preventScroll:true});
    });
    return () => cancelAnimationFrame(frame);
  }, [initialKind]);

  const [inspecting, setInspecting] = useState(false);
  const [inspectError, setInspectError] = useState<DisplayError>("");
  const [repoInfo, setRepoInfo] = useState<RepoInfo | null>(null);
  const [variants, setVariants] = useState<ModelVariantSummary[]>([]);
  const [selectedProjector, setSelectedProjector] = useState("");
  const [selectedVariantIndex, setSelectedVariantIndex] = useState<number>(0);

  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState<DisplayError>("");
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null);
  const [downloadProgress, setDownloadProgress] = useState<{
    file: string;
    fileIndex: number;
    fileCount: number;
    downloaded: number;
    total: number;
  } | null>(null);

  const [installed, setInstalled] = useState<ModelManifest[]>([]);
  const [loadingInstalled, setLoadingInstalled] = useState(false);
  const [installedError, setInstalledError] = useState<DisplayError>("");

  const [activationMode, setActivationMode] = useState("vulkan");
  const [activatingKey, setActivatingKey] = useState<string | null>(null);
  const [activationStatus, setActivationStatus] = useState<ActivationFeedback | null>(null);

  const activeDownloadRef = useRef<string | null>(null);
  const inspectSeqRef = useRef<number>(0);
  const mountedRef = useRef<boolean>(true);

  // Invalidate previous inspection results when inputs change
  const handleKindChange = (newKind: ModelKind) => {
    setKind(newKind);
    inspectSeqRef.current += 1;
    setRepoInfo(null);
    setVariants([]);
    setSelectedProjector("");
    setSelectedVariantIndex(0);
    setInspectError("");
  };

  const handleRepoIdChange = (newRepo: string) => {
    setRepoId(newRepo);
    inspectSeqRef.current += 1;
    setRepoInfo(null);
    setVariants([]);
    setSelectedProjector("");
    setSelectedVariantIndex(0);
    setInspectError("");
  };

  const handleRevisionChange = (newRev: string) => {
    setRevision(newRev);
    inspectSeqRef.current += 1;
    setRepoInfo(null);
    setVariants([]);
    setSelectedProjector("");
    setSelectedVariantIndex(0);
    setInspectError("");
  };

  const refreshInstalled = async () => {
    setLoadingInstalled(true);
    setInstalledError("");
    try {
      const list = await listInstalledModels(feedbackDeps);
      if (mountedRef.current) setInstalled(list);
    } catch (err) {
      if (mountedRef.current) setInstalledError(displayError(err));
    } finally {
      if (mountedRef.current) setLoadingInstalled(false);
    }
  };

  useEffect(() => {
    mountedRef.current = true;
    void listInstalledModels(feedbackDeps)
      .then((list) => {
        if (mountedRef.current) setInstalled(list);
      })
      .catch((err) => {
        if (mountedRef.current) setInstalledError(displayError(err));
      });

    return () => {
      mountedRef.current = false;
      const taskToCancel = activeDownloadRef.current;
      if (taskToCancel) {
        void cancelModelDownload(taskToCancel, feedbackDeps).catch(() => {});
      }
    };
  }, []);

  const handleInspect = async () => {
    const trimmedRepo = repoId.trim();
    if (!trimmedRepo) return;

    const seq = ++inspectSeqRef.current;
    const inspectKind = kind;
    const inspectRevision = revision.trim() || null;

    setInspecting(true);
    setInspectError("");
    setRepoInfo(null);
    setVariants([]);
    setSelectedProjector("");
    setSelectedVariantIndex(0);

    try {
      const info = await fetchRepoInfo(trimmedRepo, inspectRevision, feedbackDeps);
      if (!mountedRef.current || inspectSeqRef.current !== seq) return;

      const matches = await matchBundles(inspectKind, info.files, feedbackDeps);
      if (!mountedRef.current || inspectSeqRef.current !== seq) return;

      const summaries = summarizeBundleMatches(matches, info.files);
      if (!mountedRef.current || inspectSeqRef.current !== seq) return;

      setRepoInfo(info);
      setVariants(summaries);
      if (summaries.length === 0 && !(inspectKind === "vision" && visionProjectorFiles(info.files).length > 1)) {
        setInspectError(
          { source: "jarvis", message: "No compatible bundles found for the selected kind in this repository.", values: [] },
        );
      }
    } catch (err) {
      if (!mountedRef.current || inspectSeqRef.current !== seq) return;
      setInspectError(displayError(err));
    } finally {
      if (mountedRef.current && inspectSeqRef.current === seq) {
        setInspecting(false);
      }
    }
  };

  const handleProjector = async (path: string) => {
    if (!repoInfo || kind !== "vision") return;
    const info = repoInfo;
    const seq = ++inspectSeqRef.current;
    setSelectedProjector(path);
    setVariants([]);
    setSelectedVariantIndex(0);
    setInspectError("");
    setInspecting(true);
    try {
      if (!path) return;
      const matches = await matchBundles("vision", info.files, feedbackDeps, path);
      if (mountedRef.current && inspectSeqRef.current === seq) setVariants(summarizeBundleMatches(matches, info.files));
    } catch (error) {
      if (mountedRef.current && inspectSeqRef.current === seq) setInspectError(displayError(error));
    } finally {
      if (mountedRef.current && inspectSeqRef.current === seq) setInspecting(false);
    }
  };

  const handleStartDownload = async () => {
    if (!repoInfo || variants.length === 0) return;
    const variant = variants[selectedVariantIndex];
    if (!variant) return;

    const confirmed = await confirmAction(
      t(
        "Confirm download of {0} file(s) (approx {1})?\n\nRevision: {2}",
        variant.files.length,
        variant.hasUnknownSize ? t("Unknown size") : formatBytes(variant.totalBytes),
        repoInfo.revision,
      ),
    );
    if (!confirmed) return;

    const taskId =
      typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
        ? crypto.randomUUID()
        : `dl-${Date.now()}`;

    setActiveTaskId(taskId);
    activeDownloadRef.current = taskId;
    setDownloading(true);
    setDownloadError("");
    setDownloadProgress(null);

    // Pin inspected repo, revision, and variant kind
    const downloadKind = variant.kind;
    const downloadRepoId = repoInfo.repo_id;
    const downloadRevision = repoInfo.revision;
    const downloadDescriptorId = variant.descriptorId;
    const downloadFiles = variant.files;

    try {
      await downloadModelBundle(
        {
          kind: downloadKind,
          repoId: downloadRepoId,
          revision: downloadRevision,
          descriptorId: downloadDescriptorId,
          files: downloadFiles,
          requestId: taskId,
        },
        (p) => {
          if (!mountedRef.current) return;
          setDownloadProgress({
            file: p.file,
            fileIndex: p.file_index,
            fileCount: p.file_count,
            downloaded: p.downloaded,
            total: p.total,
          });
        },
        feedbackDeps,
      );
      if (mountedRef.current) {
        await refreshInstalled();
      }
    } catch (err) {
      if (mountedRef.current) {
        setDownloadError(displayError(err));
      }
    } finally {
      if (mountedRef.current) {
        setDownloading(false);
        setActiveTaskId(null);
      }
      activeDownloadRef.current = null;
    }
  };

  const handleCancelDownload = async () => {
    if (!activeTaskId) return;
    try {
      await cancelModelDownload(activeTaskId, feedbackDeps);
    } catch (err) {
      setDownloadError(displayError(err));
    }
  };

  const handleDelete = async (item: ModelManifest) => {
    const confirmed = await confirmAction(
      t("Delete installed model '{0}' (revision: {1})?", item.repo_id, item.revision.slice(0, 8)),
    );
    if (!confirmed) return;

    try {
      await deleteInstalledModel(item.kind, item.repo_id, item.revision, feedbackDeps);
      await refreshInstalled();
    } catch (err) {
      setInstalledError(displayError(err));
    }
  };

  const handleActivate = async (item: ModelManifest) => {
    const key = `${item.kind}:${item.repo_id}:${item.revision}`;
    setActivatingKey(key);
    setActivationStatus({ template: "Activating model…" });
    try {
      const res = await activateModel(item.kind, item.repo_id, item.revision, activationMode, feedbackDeps);
      if (res && res.running) {
        setActivationStatus(
          { template: "Activated '{0}' successfully.", values: [item.repo_id] },
        );
      } else {
        setActivationStatus(
          item.kind === "tts"
            ? { template: res?.family === "piper"
                ? "Piper weights selected. Choose Piper and a speaker in Speech & Voice."
                : "TTS weights selected. Choose Supertonic and a speaker in Speech & Voice." }
            : { template: "Activation configured: {0}", values: [item.repo_id] },
        );
      }
    } catch (err) {
      setActivationStatus(
        { error: displayError(err) },
      );
    } finally {
      setActivatingKey(null);
    }
  };

  const selectedVariant = variants[selectedVariantIndex];
  const diskKey=repoInfo && selectedVariant ? JSON.stringify([repoInfo.repo_id,repoInfo.revision,selectedVariant.descriptorId,selectedVariant.files]) : "";
  const disk=diskResult?.key===diskKey ? diskResult.value : undefined;
  const diskError=diskResult?.key===diskKey ? diskResult.error : undefined;
  const memoryBytes=(bytes:number|null|undefined)=>bytes==null?t("Unknown"):formatBytes(bytes);
  useEffect(()=>{
    let active=true;
    if(!repoInfo || !selectedVariant) return;
    void inspectTargetDisk(repoInfo,selectedVariant,feedbackDeps).then(value=>{if(active)setDiskResult({key:diskKey,value});})
      .catch(error=>{if(active)setDiskResult({key:diskKey,error:displayError(error)});});
    return ()=>{active=false;};
  },[diskKey,diskRefresh]);


  return (
    <details
      ref={libraryRef}
      tabIndex={-1}
      open={expanded}
      onToggle={event => setExpanded(event.currentTarget.open)}
      className="rounded-xl border border-border/60 bg-card/60 p-4"
      data-model-library
    >
      <summary className="cursor-pointer text-sm font-medium">
        {t("Model Library · Download & Manage")}
        <span className="ml-2 text-xs text-muted-foreground">
          {installed.length > 0
            ? t("{0} installed", installed.length)
            : t("Manage local weights")}
        </span>
      </summary>

      <div className="mt-4 flex flex-col gap-5">
        {/* Hub Inspection and Download Controls */}
        <div className="flex flex-col gap-3 rounded-lg border border-border/40 bg-background/50 p-3.5">
          <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            {t("Inspect & Download from HuggingFace")}
          </span>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <label className="flex flex-col gap-1 text-xs">
              <span className="text-muted-foreground">{t("Kind")}</span>
              <select
                aria-label={t("Kind")}
                className="h-8 rounded-md border border-input bg-background px-2 text-xs"
                value={kind}
                disabled={inspecting || downloading}
                onChange={(e) => handleKindChange(e.target.value as ModelKind)}
              >
                <option value="llm">{t("Language Model (LLM)")}</option>
                <option value="vision">{t("Vision Model")}</option>
                <option value="embedding">{t("Embedding Model")}</option>
                <option value="tts">{t("TTS Voice Model")}</option>
              </select>
            </label>

            <label className="flex flex-col gap-1 text-xs sm:col-span-2">
              <span className="text-muted-foreground">{t("Repository ID")}</span>
              <Input
                placeholder={translateMessage("e.g. Qwen/Qwen2.5-0.5B-Instruct-GGUF")}
                value={repoId}
                disabled={inspecting || downloading}
                onChange={(e) => handleRepoIdChange(e.target.value)}
                className="h-8 text-xs font-mono"
              />
            </label>
          </div>

          <div className="flex flex-wrap items-end gap-3">
            <label className="flex flex-1 flex-col gap-1 text-xs min-w-[200px]">
              <span className="text-muted-foreground">
                {t("Revision / Commit / Branch (optional)")}
              </span>
              <Input
                placeholder={translateMessage("main or commit sha")}
                value={revision}
                disabled={inspecting || downloading}
                onChange={(e) => handleRevisionChange(e.target.value)}
                className="h-8 text-xs font-mono"
              />
            </label>

            <Button
              size="sm"
              disabled={inspecting || downloading || !repoId.trim()}
              onClick={() => void handleInspect()}
              className="h-8 px-4 text-xs"
            >
              {inspecting ? t("Inspecting…") : t("Inspect Repository")}
            </Button>
          </div>

          {inspectError && (
            <p role="alert" className="text-xs text-destructive break-words">
              {errorText(inspectError, locale)}
            </p>
          )}

          {/* Repo Info and Warnings */}
          {repoInfo && (
            <div className="mt-2 flex flex-col gap-2 rounded border border-border/50 bg-muted/20 p-3 text-xs">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-semibold text-foreground">
                  {repoInfo.repo_id}
                </span>
                <span className="font-mono text-muted-foreground">
                  {t("Revision:")} {repoInfo.revision.slice(0, 10)}
                </span>
              </div>

              <div className="flex flex-wrap items-center gap-2">
                {repoInfo.license && (
                  <Badge variant="outline" className="text-[10px]">
                    {t("License:")} {repoInfo.license}
                  </Badge>
                )}
                {repoInfo.gated && (
                  <Badge variant="destructive" className="text-[10px]">
                    {t("Gated Repository")}
                  </Badge>
                )}
                <span className="text-muted-foreground">
                  {t("Available repo files:")} {repoInfo.files.length}
                </span>
              </div>

              {kind === "vision" && visionProjectorFiles(repoInfo.files).length > 1 && (
                <label className="flex flex-col gap-2">
                  {t("Vision projector")}
                  <select className="h-8 rounded-md border border-input bg-background px-2 text-xs" aria-label={t("Vision projector")} value={selectedProjector} disabled={downloading} onChange={event => void handleProjector(event.target.value)}>
                    <option value="">{t("Choose the projector documented for this base model")}</option>
                    {visionProjectorFiles(repoInfo.files).map(path => <option key={path} value={path}>{path}</option>)}
                  </select>
                  <span>{t("Projector compatibility must be confirmed from the publisher's model documentation. Filenames alone do not prove compatibility.")}</span>
                </label>
              )}

              {repoInfo.gated && (
                <p className="text-[11px] text-destructive">
                  {t(
                    "Warning: This repository requires authentication or access approval from the publisher.",
                  )}
                </p>
              )}

              {/* Variant Picker */}
              {variants.length > 0 && (
                <div className="mt-2 flex flex-col gap-2">
                  <span className="font-medium text-muted-foreground">
                    {t("Select Variant ({0} found):", variants.length)}
                  </span>

                  <div className="flex flex-col gap-1.5 max-h-48 overflow-y-auto">
                    {variants.map((v, idx) => (
                      <label
                        key={idx}
                        className={`flex items-start gap-2.5 rounded border p-2 cursor-pointer transition-colors ${
                          selectedVariantIndex === idx
                            ? "border-primary bg-primary/10"
                            : "border-border/50 bg-background/60 hover:bg-muted/40"
                        }`}
                      >
                        <input
                          type="radio"
                          name="model-variant"
                          checked={selectedVariantIndex === idx}
                          disabled={downloading}
                          onChange={() => setSelectedVariantIndex(idx)}
                          className="mt-0.5"
                        />
                        <div className="flex flex-1 flex-col gap-1 min-w-0">
                          <div className="flex items-center justify-between gap-2">
                            <span className="font-mono text-[11px] font-medium truncate">
                              {v.files.join(", ")}
                            </span>
                            <span className="shrink-0 text-[10.5px] text-muted-foreground font-mono">
                              {v.hasUnknownSize
                                ? t("Size unknown")
                                : formatBytes(v.totalBytes)}
                            </span>
                          </div>
                          <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
                            <span>{v.descriptorId}</span>
                            {v.descriptorId === "tts-piper-onnx" && (
                              <Badge
                                variant="outline"
                                className="text-[9px] border-amber-500/40 text-amber-500"
                              >
                                {t("Piper · CPU")}
                              </Badge>
                            )}
                          </div>
                        </div>
                      </label>
                    ))}
                  </div>

                  {selectedVariant && selectedVariant.hasUnknownSize && (
                    <p className="text-[11px] text-amber-500">
                      {t(
                        "Notice: Some files do not have published size metadata. Actual download size may vary.",
                      )}
                    </p>
                  )}

                  <section aria-label={t("Target disk space")} className="text-xs">
                    {disk ? <>
                      <p>{t("Required target space: {0} · Free: {1}",formatBytes(disk.required_bytes),formatBytes(disk.available_bytes))}</p>
                      {disk.resumable_bytes>0 && <p>{t("Resumable data: {0}",formatBytes(disk.resumable_bytes))}</p>}
                      {!disk.published_sizes_known && <p>{t("Some sizes are unknown; disk checks reserve the maximum permitted file sizes.")}</p>}
                      {!disk.sufficient && <p role="alert">{t("Not enough target disk space. Free space before downloading.")}</p>}
                    </> : <p>{diskError ? errorText(diskError, locale) : t("Checking target disk space…")}</p>}
                    <Button size="sm" variant="ghost" disabled={downloading} onClick={()=>setDiskRefresh(value=>value+1)}>{t("Refresh storage and memory checks")}</Button>
                  </section>
                  <section aria-label={t("RAM and VRAM guidance")} className="text-xs space-y-1 mt-2">
                    <h4>{t("RAM and VRAM guidance")}</h4>
                    {disk?.hardware ? <>
                      <p>{t("Selected file bytes: {0}",memoryBytes(disk.hardware.selected_file_bytes))}</p>
                      <p>{t("Planning budget guide: {0}",memoryBytes(disk.hardware.planning_bytes))}</p>
                      <p>{t("Planning margin only: file bytes + max(1 GiB, 25%). This is not a runtime allocation estimate.")}</p>
                      <p>{t("Physical RAM — available: {0} · total: {1}",memoryBytes(disk.hardware.ram.available_bytes),memoryBytes(disk.hardware.ram.total_bytes))}</p>
                      <p>{t(memoryGuidance(disk.hardware.ram.status))}</p>
                      {disk.hardware.gpus.map((gpu,index)=><div key={index}>
                        <p>{gpu.name}: {t("Dedicated VRAM — total: {0} · free: {1}",memoryBytes(gpu.total_bytes),memoryBytes(gpu.available_bytes))}</p>
                        <p>{t(memoryGuidance(gpu.status))}</p>
                      </div>)}
                      {disk.hardware.gpus.length===0 && <p>{t("VRAM telemetry is unavailable. This does not mean no GPU is installed.")}</p>}
                      {disk.hardware.gpus.length>0 && <p>{t("DXGI reports dedicated capacity only. Free VRAM is unknown; shared system memory is not added to it.")}</p>}
                    </> : <p>{t("Memory telemetry is unavailable in this runtime.")}</p>}
                    <p>{t("Context length, KV cache, compute buffers, vision projectors and concurrent requests may need much more memory. No exact fit is inferred from a model name.")}</p>
                    <p>{t("Advisory only: CPU execution or partial offload may work even when dedicated VRAM is smaller than the files. Memory guidance does not block downloading.")}</p>
                  </section>
                  <div className="mt-2 flex items-center gap-3">
                    <Button
                      size="sm"
                      disabled={downloading || !selectedVariant || !disk?.sufficient}
                      onClick={() => void handleStartDownload()}
                      className="h-8 px-4 text-xs"
                    >
                      {t("Download Selected Variant")}
                    </Button>

                    {downloading && (
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => void handleCancelDownload()}
                        className="h-8 px-3 text-xs text-destructive hover:bg-destructive/10"
                      >
                        {t("Cancel Download")}
                      </Button>
                    )}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Download progress UI */}
          {downloading && downloadProgress && (
            <div className="mt-2 flex flex-col gap-1.5 rounded border border-primary/40 bg-primary/5 p-3 text-xs">
              <div className="flex items-center justify-between">
                <span className="font-mono truncate">
                  {downloadProgress.file} ({downloadProgress.fileIndex + 1}/
                  {downloadProgress.fileCount})
                </span>
                <span className="font-mono text-muted-foreground">
                  {formatBytes(downloadProgress.downloaded)} /{" "}
                  {downloadProgress.total > 0
                    ? formatBytes(downloadProgress.total)
                    : t("indeterminate")}
                </span>
              </div>
              <div className="h-1.5 w-full overflow-hidden rounded bg-secondary">
                <div
                  className="h-full bg-primary transition-all duration-200"
                  style={{
                    width: `${calculateProgress(
                      downloadProgress.downloaded,
                      downloadProgress.total,
                    )}%`,
                  }}
                />
              </div>
            </div>
          )}

          {downloadError && (
            <p role="alert" className="text-xs text-destructive break-words">
              {errorText(downloadError, locale)}
            </p>
          )}
        </div>

        {/* Installed models list */}
        <div className="flex flex-col gap-3 rounded-lg border border-border/40 bg-background/50 p-3.5">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              {t("Installed Managed Models")}
            </span>
            <Button
              size="sm"
              variant="ghost"
              disabled={loadingInstalled}
              onClick={() => void refreshInstalled()}
              className="h-6 px-2 text-[11px] text-muted-foreground"
            >
              {loadingInstalled ? t("Refreshing…") : t("Refresh")}
            </Button>
          </div>

          <label className="flex items-center gap-3 text-xs">
            {t("Activation device")}
            <select aria-label={t("Activation device")} value={activationMode}
              disabled={Boolean(activatingKey) || downloading}
              onChange={event => setActivationMode(event.target.value)}
              className="rounded border border-border bg-background p-1.5 text-foreground">
              <option value="vulkan">{translateMessage("Vulkan")}</option><option value="cpu">{translateMessage("CPU")}</option>
            </select>
          </label>

          {installedError && (
            <p role="alert" className="text-xs text-destructive break-words">
              {errorText(installedError, locale)}
            </p>
          )}

          {activationStatus && (
            <p role="status" className="text-xs text-primary break-words">
              {"error" in activationStatus
                ? t("Failed to activate: {0}", errorText(activationStatus.error, locale))
                : t(activationStatus.template, ...(activationStatus.values ?? []))}
            </p>
          )}

          {installed.length === 0 ? (
            <p className="text-xs text-muted-foreground italic">
              {t("No models currently installed in managed storage.")}
            </p>
          ) : (
            <div className="flex flex-col gap-2">
              {installed.map((item, i) => {
                const totalBytes = item.files.reduce((a, b) => a + b.size, 0);
                const downloadOnly = item.kind === "tts" && !["tts-supertonic-gguf", "tts-piper-onnx"].includes(item.bundle_id ?? "");
                const isActivating =
                  activatingKey === `${item.kind}:${item.repo_id}:${item.revision}`;

                return (
                  <div
                    key={`${item.kind}:${item.repo_id}:${item.revision}:${i}`}
                    data-installed-model={`${item.kind}:${item.repo_id}:${item.revision}`}
                    className="flex flex-col gap-2 rounded border border-border/60 bg-card/40 p-3 text-xs sm:flex-row sm:items-center sm:justify-between"
                  >
                    <div className="flex flex-col gap-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <Badge variant="outline" className="text-[10px] uppercase font-mono">
                          {item.kind}
                        </Badge>
                        <span className="font-semibold text-foreground truncate">
                          {item.repo_id}
                        </span>
                      </div>
                      <div className="flex flex-wrap items-center gap-2 font-mono text-[10.5px] text-muted-foreground">
                        <span>{t("rev:")} {item.revision.slice(0, 10)}</span>
                        <span>•</span>
                        <span>{item.files.length} {t("file(s)")}</span>
                        <span>•</span>
                        <span>{formatBytes(totalBytes)}</span>
                      </div>
                      <div className="font-mono text-[10px] text-muted-foreground/80 truncate">
                        {item.files.map((f) => f.path).join(", ")}
                      </div>
                    </div>

                    <div className="flex items-center gap-2 shrink-0">
                      {item.bundle_id === "tts-piper-onnx" && <span className="text-[11px] text-muted-foreground">{t("Piper ONNX requires CPU mode")}</span>}
                      {downloadOnly ? (
                        <span className="text-[11px] text-muted-foreground italic">
                          {t("Download only — runtime activation pending")}
                        </span>
                      ) : (
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={isActivating || downloading || (item.bundle_id === "tts-piper-onnx" && activationMode !== "cpu")}
                          onClick={() => void handleActivate(item)}
                          className="h-7 px-2.5 text-xs"
                        >
                          {isActivating ? t("Activating…") : t("Activate")}
                        </Button>
                      )}

                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={downloading}
                        onClick={() => void handleDelete(item)}
                        className="h-7 px-2 text-xs text-muted-foreground hover:text-destructive"
                      >
                        {t("Delete")}
                      </Button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </details>
  );
}
