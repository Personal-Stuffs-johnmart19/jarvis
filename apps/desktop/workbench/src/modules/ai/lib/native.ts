import { invoke } from "@/host/core";
import { currentWorkspaceEnv, type WorkspaceEnv } from "@/modules/workspace";

export type ReadResult =
  | { kind: "text"; content: string; size: number }
  | { kind: "binary"; size: number }
  | { kind: "toolarge"; size: number; limit: number };

export type DirEntry = {
  name: string;
  kind: "file" | "dir" | "symlink";
  size: number;
  mtime: number;
  gitignored: boolean;
};

export type CommandOutput = {
  stdout: string;
  stderr: string;
  exit_code: number | null;
  timed_out: boolean;
  truncated: boolean;
};

type GrepHit = {
  path: string;
  rel: string;
  line: number;
  text: string;
};

export type GrepResponse = {
  hits: GrepHit[];
  truncated: boolean;
  files_scanned: number;
};

type GlobHit = { path: string; rel: string };
export type GlobResponse = { hits: GlobHit[]; truncated: boolean };

export const native = {
  workspaceCurrentDir: () => invoke<string>("workspace_current_dir"),
  workspaceAuthorize: (path: string) =>
    invoke<string>("workspace_authorize", {
      path,
      workspace: currentWorkspaceEnv(),
    }),
  readProjectMemory: (root: string, checkedPath: string, workspace: WorkspaceEnv) =>
    invoke<string | null>("fs_read_project_memory", {
      root,
      checkedPath,
      workspace,
    }),
  readFile: (path: string, workspace:WorkspaceEnv=currentWorkspaceEnv()) =>
    invoke<ReadResult>("ai_read_file", {
      path,
      workspace,
    }),
  readSnapshot: (path: string, workspace:WorkspaceEnv=currentWorkspaceEnv()) =>
    invoke<ReadResult | { kind: "missing" }>("fs_read_file", { path, workspace, allowMissing: true }),
  writeFile: (path: string, content: string, workspace:WorkspaceEnv=currentWorkspaceEnv()) =>
    invoke<void>("fs_write_file", {
      path,
      content,
      workspace,
    }),
  canonicalize: (path: string, workspace:WorkspaceEnv=currentWorkspaceEnv(), allowMissing=false) =>
    invoke<string>("fs_canonicalize", {
      path,
      workspace,
      allowMissing,
    }),
  createFile: (path: string) =>
    invoke<void>("fs_create_file", { path, workspace: currentWorkspaceEnv() }),
  createDir: (path: string, workspace:WorkspaceEnv=currentWorkspaceEnv()) =>
    invoke<void>("fs_create_dir", { path, workspace }),
  // AI tooling never sees dot-prefixed entries regardless of the user's
  // explorer preference — keeps .git / .env / .ssh out of agent context.
  readDir: (path: string, workspace:WorkspaceEnv=currentWorkspaceEnv()) =>
    invoke<DirEntry[]>("ai_read_dir", {
      path,
      showHidden: false,
      workspace,
    }),
  grep: (params: {
    pattern: string;
    root: string;
    glob?: string[];
    caseInsensitive?: boolean;
    maxResults?: number;
  }, workspace:WorkspaceEnv=currentWorkspaceEnv()) =>
    invoke<GrepResponse>("ai_grep", {
      pattern: params.pattern,
      root: params.root,
      glob: params.glob ?? null,
      caseInsensitive: params.caseInsensitive ?? null,
      maxResults: params.maxResults ?? null,
      workspace,
    }),
  glob: (params: { pattern: string; root: string; maxResults?: number }, workspace:WorkspaceEnv=currentWorkspaceEnv()) =>
    invoke<GlobResponse>("ai_glob", {
      pattern: params.pattern,
      root: params.root,
      maxResults: params.maxResults ?? null,
      workspace,
    }),
  runCommand: (
    command: string,
    cwd?: string | null,
    timeoutSecs?: number,
    requestId?: string | null,
  ) =>
    invoke<CommandOutput>("shell_run_command", {
      command,
      cwd: cwd ?? null,
      timeoutSecs: timeoutSecs ?? null,
      workspace: currentWorkspaceEnv(),
      requestId: requestId ?? null,
    }),
  shellRunCancel: (requestId: string) =>
    invoke<void>("shell_run_cancel", { requestId }),

  shellSessionOpen: (cwd?: string | null) =>
    invoke<number>("shell_session_open", {
      cwd: cwd ?? null,
      workspace: currentWorkspaceEnv(),
    }),
  shellSessionRun: (
    id: number,
    command: string,
    cwd?: string | null,
    timeoutSecs?: number,
    requestId?: string | null,
  ) =>
    invoke<{
      stdout: string;
      stderr: string;
      exit_code: number | null;
      timed_out: boolean;
      truncated: boolean;
      cwd_after: string;
    }>("shell_session_run", {
      id,
      command,
      cwd: cwd ?? null,
      timeoutSecs: timeoutSecs ?? null,
      workspace: currentWorkspaceEnv(),
      requestId: requestId ?? null,
    }),
  shellSessionClose: (id: number) =>
    invoke<void>("shell_session_close", { id }),
  shellBgSpawn: (command: string, cwd?: string | null) =>
    invoke<number>("shell_bg_spawn", {
      command,
      cwd: cwd ?? null,
      workspace: currentWorkspaceEnv(),
    }),
  shellBgLogs: (handle: number, sinceOffset?: number) =>
    invoke<{
      bytes: string;
      next_offset: number;
      dropped: number;
      exited: boolean;
      exit_code: number | null;
    }>("shell_bg_logs", { handle, sinceOffset: sinceOffset ?? null }),
  shellBgKill: (handle: number) => invoke<void>("shell_bg_kill", { handle }),
  shellBgList: () =>
    invoke<
      {
        handle: number;
        command: string;
        cwd: string | null;
        started_at_ms: number;
        exited: boolean;
        exit_code: number | null;
      }[]
    >("shell_bg_list"),
};
