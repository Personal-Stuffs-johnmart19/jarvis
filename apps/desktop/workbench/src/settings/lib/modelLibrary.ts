import type { ModelId } from "@/modules/ai/config";

export type ModelKind = "llm" | "vision" | "embedding" | "tts";

export interface RepoFile {
  path: string;
  size?: number | null;
  sha256?: string | null;
}

export interface RepoInfo {
  repo_id: string;
  revision: string;
  license?: string | null;
  gated: boolean;
  files: RepoFile[];
  total_size: number;
}

export interface BundleDescriptor {
  id: string;
  kind: ModelKind;
  label: string;
  requirement: string;
}

export interface BundleMatch {
  descriptor_id: string;
  kind: ModelKind;
  files: string[];
}

export interface ManifestFile {
  path: string;
  size: number;
  sha256: string;
}

export interface ModelManifest {
  owner: string;
  complete: boolean;
  repo_id: string;
  revision: string;
  kind: ModelKind;
  bundle_id: string;
  license?: string | null;
  files: ManifestFile[];
}

export interface ModelDownloadRequest {
  kind: ModelKind;
  repo_id: string;
  revision?: string | null;
  descriptor_id: string;
  files: string[];
  request_id?: string | null;
}

export interface DownloadProgressPayload {
  task_id: string;
  file: string;
  file_index: number;
  file_count: number;
  downloaded: number;
  total: number;
}

export interface ActivateResult {
  family?: 'supertonic' | 'piper';
  kind: string;
  baseURL?: string;
  modelId?: string;
  context?: number;
  configured?: boolean;
  running?: boolean;
}

export interface ModelVariantSummary {
  descriptorId: string;
  kind: ModelKind;
  files: string[];
  totalBytes: number;
  hasUnknownSize: boolean;
}

export interface CustomEndpointRecord {
  id: string;
  name: string;
  baseURL: string;
  modelId: string;
  contextLimit: number;
}

export interface ModelLibraryDeps {
  invoke: <T = unknown>(cmd: string, args?: Record<string, unknown>) => Promise<T>;
  listen: <T = unknown>(
    event: string,
    handler: (event: { payload: T }) => void,
  ) => Promise<() => void>;
  getCustomEndpoints?: () => CustomEndpointRecord[];
  setCustomEndpoints?: (endpoints: CustomEndpointRecord[]) => Promise<void> | void;
  setDefaultModel?: (modelId: string) => Promise<void> | void;
  randomUUID?: () => string;
}

/**
 * Summarize bundle matches with explicit sizes and unknown size warnings.
 */
export function summarizeBundleMatches(
  matches: BundleMatch[],
  repoFiles: RepoFile[],
): ModelVariantSummary[] {
  const fileMap = new Map<string, RepoFile>();
  for (const rf of repoFiles) {
    fileMap.set(rf.path, rf);
  }

  return matches.map((m) => {
    let totalBytes = 0;
    let hasUnknownSize = false;

    for (const f of m.files) {
      const info = fileMap.get(f);
      if (info && typeof info.size === "number" && info.size >= 0) {
        totalBytes += info.size;
      } else {
        hasUnknownSize = true;
      }
    }

    return {
      descriptorId: m.descriptor_id,
      kind: m.kind,
      files: m.files,
      totalBytes,
      hasUnknownSize,
    };
  });
}

/**
 * Format bytes into readable format.
 */
export function formatBytes(bytes: number): string {
  if (bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  const val = bytes / Math.pow(1024, i);
  return `${val.toFixed(i === 0 ? 0 : 2)} ${units[i]}`;
}

/**
 * Format a progress percentage (0 - 100).
 */
export function calculateProgress(downloaded: number, total: number): number {
  if (!total || total <= 0) return 0;
  return Math.min(100, Math.max(0, Math.round((downloaded / total) * 100)));
}

/**
 * Helper to generate random UUIDs.
 */
export function generateRequestId(customUuidFn?: () => string): string {
  if (customUuidFn) return customUuidFn();
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `req-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

/**
 * Query repo information via Tauri IPC.
 */
export async function fetchRepoInfo(
  repoId: string,
  revision?: string | null,
  deps?: Pick<ModelLibraryDeps, "invoke">,
): Promise<RepoInfo> {
  const invokeFn = deps?.invoke || (await getHostCore()).invoke;
  return await invokeFn<RepoInfo>("model_fetch_repo_info", {
    repoId: repoId.trim(),
    revision: revision?.trim() || null,
  });
}

/**
 * Match bundles against repository files.
 */
export function visionProjectorFiles(files: RepoFile[]): string[] {
  return [...new Set(files.map(file => file.path).filter(path => /\.gguf$/i.test(path) && /mmproj/i.test(path.split("/").at(-1) ?? "") && !path.toLowerCase().includes("-of-")))].sort();
}

export async function matchBundles(
  kind: ModelKind,
  files: RepoFile[],
  deps?: Pick<ModelLibraryDeps, "invoke">,
  projectorPath?: string,
): Promise<BundleMatch[]> {
  const invokeFn = deps?.invoke || (await getHostCore()).invoke;
  return await invokeFn<BundleMatch[]>("model_match_bundles", {
    kind,
    files,
    ...(projectorPath ? { projectorPath } : {}),
  });
}

/**
 * List completed model installations.
 */
export async function listInstalledModels(
  deps?: Pick<ModelLibraryDeps, "invoke">,
): Promise<ModelManifest[]> {
  const invokeFn = deps?.invoke || (await getHostCore()).invoke;
  return await invokeFn<ModelManifest[]>("model_list_installed");
}

/**
 * Delete an installed model.
 */
export async function deleteInstalledModel(
  kind: ModelKind,
  repoId: string,
  revision: string,
  deps?: Pick<ModelLibraryDeps, "invoke">,
): Promise<void> {
  const invokeFn = deps?.invoke || (await getHostCore()).invoke;
  await invokeFn("model_delete", {
    kind,
    repoId,
    revision,
  });
}

/**
 * Cancel an active model download request.
 */
export async function cancelModelDownload(
  taskId: string,
  deps?: Pick<ModelLibraryDeps, "invoke">,
): Promise<boolean> {
  const invokeFn = deps?.invoke || (await getHostCore()).invoke;
  return await invokeFn<boolean>("model_download_cancel", {
    taskId,
  });
}

/**
 * Start downloading a model bundle.
 * Manages request UUID, progress listener, and ensures unlisten clean up on success/error/cancel.
 */
export async function downloadModelBundle(
  request: {
    kind: ModelKind;
    repoId: string;
    revision: string;
    descriptorId: string;
    files: string[];
    requestId?: string;
  },
  onProgress?: (progress: DownloadProgressPayload) => void,
  deps?: Pick<ModelLibraryDeps, "invoke" | "listen" | "randomUUID">,
): Promise<ModelManifest> {
  const requestId = request.requestId || generateRequestId(deps?.randomUUID);
  const listenFn = deps?.listen || (await getHostEvent()).listen;
  const invokeFn = deps?.invoke || (await getHostCore()).invoke;

  let unlisten: (() => void) | null = null;
  if (onProgress) {
    unlisten = await listenFn<DownloadProgressPayload>(
      "model-download:progress",
      (event) => {
        if (event?.payload?.task_id === requestId) {
          onProgress(event.payload);
        }
      },
    );
  }

  try {
    const manifest = await invokeFn<ModelManifest>("model_download_start", {
      request: {
        kind: request.kind,
        repo_id: request.repoId,
        revision: request.revision,
        descriptor_id: request.descriptorId,
        files: request.files,
        request_id: requestId,
      },
    });
    return manifest;
  } finally {
    if (unlisten) {
      unlisten();
    }
  }
}

/**
 * Activate a model via backend RPC method `models.activate`.
 * Stores the managed Chat / Vision identity without mutating a custom endpoint.
 * Lazy backend may return configured=true, running=false on on-demand start.
 */
export async function activateModel(
  kind: ModelKind,
  repoId: string,
  revision: string,
  mode: string = "vulkan",
  deps?: ModelLibraryDeps,
): Promise<ActivateResult> {
  const invokeFn = deps?.invoke || (await getHostCore()).invoke;
  const result = await invokeFn<ActivateResult>("backend", {
    method: "models.activate",
    params: {
      kind,
      repo_id: repoId,
      revision,
      mode,
    },
  });

  if ((kind === "llm" || kind === "vision") && result.configured) {
    const expected = `jarvis-managed:${kind}:${repoId}@${revision}`;
    if (result.modelId !== expected || !/^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$/.test(revision)) throw new Error("Activated managed model identity does not match the requested bundle");
    if (deps?.setDefaultModel) await deps.setDefaultModel(result.modelId);
    else {
      const { setDefaultModel } = await getPreferencesStore();
      await setDefaultModel(result.modelId as ModelId);
    }
  }

  return result;
}

// Lazy dynamic imports for browser / host runtime
async function getHostCore() {
  return await import("@/host/core");
}

async function getHostEvent() {
  return await import("@/host/event");
}

async function getPreferencesStore() {
  return await import("@/modules/settings/store");
}


export type MemoryStatus = "unknown" | "files_exceed_capacity" | "files_exceed_available" | "limited_headroom" | "planning_headroom" | "capacity_only";
export type MemoryReading = {name:string; total_bytes:number|null; available_bytes:number|null; status:MemoryStatus};
export type MemoryPreflight = {selected_file_bytes:number|null; planning_bytes:number|null; ram:MemoryReading; gpus:MemoryReading[]; gpu_telemetry:string};
export type DiskPreflight = {required_bytes:number; available_bytes:number; resumable_bytes:number; published_sizes_known:boolean; sufficient:boolean; hardware?:MemoryPreflight|null};

/** Advisory labels never promise a fit or prohibit CPU/partial offload. */
export function memoryGuidance(status:MemoryStatus|string):string {
  switch(status) {
    case "files_exceed_capacity": return "Selected files exceed reported memory capacity";
    case "files_exceed_available": return "Selected files exceed currently available memory";
    case "limited_headroom": return "Limited headroom for context and compute buffers";
    case "planning_headroom": return "Planning headroom; actual fit is not guaranteed";
    case "capacity_only": return "Capacity reported; available memory and actual fit are unknown";
    default: return "Unknown";
  }
}
export async function inspectTargetDisk(info:RepoInfo, variant:ModelVariantSummary, deps?:Pick<ModelLibraryDeps,"invoke">):Promise<DiskPreflight> {
  const invokeFn=deps?.invoke || (await getHostCore()).invoke;
  return invokeFn<DiskPreflight>("model_disk_preflight",{info,bundle:{kind:variant.kind,descriptor_id:variant.descriptorId,files:variant.files}});
}
