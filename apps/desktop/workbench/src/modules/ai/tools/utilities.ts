import { runCancellableProcess, isProcessAbortError } from "../lib/processExecution";
import { tool } from "ai";
import { z } from "zod";
import { invoke } from "@/host/core";
import { currentWorkspaceEnv, type WorkspaceEnv } from "@/modules/workspace";
import { native } from "../lib/native";
import {
  checkReadableCanonical,
  checkWritableCanonical,
} from "../lib/security";
import { resolvePath, type ToolContext } from "./context";
import { queuedUtility, queuedUtilityRequest } from "./queuedDispatch";
import { readOnlyRequest } from "../lib/readOnlyRequest";

function cwd(ctx: ToolContext, value?: string) {
  const base = ctx.getCwd() || ctx.getWorkspaceRoot();
  if (value?.trim()) return resolvePath(value, base);
  if (!base) throw new Error("No active workspace; pass an absolute cwd.");
  return base;
}
async function readable(path: string, base: string | null,workspace:WorkspaceEnv) {
  const checked = await checkReadableCanonical(
    resolvePath(path, base),
    path=>native.canonicalize(path,workspace),
  );
  if (!checked.ok) throw new Error(checked.reason);
  return checked.canonical;
}
async function writable(path: string, base: string | null,workspace:WorkspaceEnv) {
  const checked = await checkWritableCanonical(
    resolvePath(path, base),
    path=>native.canonicalize(path,workspace,true),
  );
  if (!checked.ok) throw new Error(checked.reason);
  return checked.canonical;
}
const options = {
  cwd: z.string().max(2048).optional(),
  timeout_secs: z.number().int().min(1).max(300).optional(),
};
export function buildUtilityTools(ctx: ToolContext) {
  const run = (
    tool: string,
    args: string[],
    directory?: string,
    timeout?: number,
    max_output?: number,
    signal?: AbortSignal,
    requestId?: string,
  ) => runCancellableProcess((requestId) =>
    invoke("utility_run", {
      requestId,
      tool,
      args,
      cwd: cwd(ctx, directory),
      timeoutSecs: timeout ?? 30,
      maxOutput: max_output ?? 8000,
      workspace: currentWorkspaceEnv(),
    }), (requestId) => native.shellRunCancel(requestId), signal, requestId?()=>requestId:undefined);
  return {
    utility_catalog: tool({
      description:
        "Utilities / utils / CLI catalogue: discover installed programs, exact executable paths, source and direct-execution availability. Use utility_run via jarvis_action to execute a program. Filter by query and paginate with cursor; defaults to 12 results to conserve local-model context. Includes rg, fd, jq, yq, git, gh, Python, Node, npm, pnpm, uv, pip, Rust/C/C++/Go/Java/.NET compilers, curl, tar, zip, 7z, ffmpeg, ffprobe, magick, tesseract, pdftotext, pdfinfo, pdftoppm, sqlite3, bash, PowerShell, WSL, pandoc and soffice. Missing tools are not installed automatically. Built-in grep/glob and ZIP tools work without external rg/zip binaries. WSL utilities use bash_run in that distribution.",
      inputSchema: z.object({
        query: z.string().max(100).optional(),
        available_only: z.boolean().optional(),
        cursor: z.number().int().min(0).optional(),
        limit: z.number().int().min(1).max(20).optional(),
      }),
      execute: async ({ query, available_only, cursor, limit }, execution) => {
        const utilities = await readOnlyRequest(() => invoke<
          {
            id: string;
            description: string;
            category: string;
            available: boolean;
            path: string | null;
          }[]
        >("utility_inventory"), execution.abortSignal);
        const needle = query?.toLowerCase().trim() ?? "";
        const matches = utilities.filter(
          (item) =>
            (!available_only || item.available) &&
            (!needle ||
              `${item.id} ${item.description} ${item.category}`
                .toLowerCase()
                .includes(needle)),
        );
        const offset = cursor ?? 0,
          size = limit ?? 12;
        return {
          utilities: matches.slice(offset, offset + size),
          total_results: matches.length,
          total_available: utilities.filter((item) => item.available).length,
          next_cursor: offset + size < matches.length ? offset + size : null,
          built_in: [
            "grep (ripgrep Rust engine)",
            "glob",
            "archive_list",
            "archive_create",
            "archive_extract",
          ],
        };
      },
    }),
    utility_run: queuedUtility(tool({
      description:
        "Run a discovered host utility with literal argv, no shell interpolation. Each args string is already one argument: never wrap it in shell quotes. Use forward slashes in Windows paths. Example: [\"eval\", \".value * 7\", \"D:/data/file.yaml\"]. Captures bounded stdout/stderr, exit code and timeout; runs separately from the user's terminal. Requires approval; this is not an OS sandbox. Prefer grep/glob for safe searches and dedicated archive tools for ZIP. For arbitrary shell pipelines or WSL, use bash_run.",
      inputSchema: z.object({
        tool: z.string().max(40),
        args: z.array(z.string().max(16000)).max(128),
        ...options,
        max_output: z.number().int().min(1024).max(32000).optional(),
      }),
      needsApproval: true,
      execute: async ({ tool, args, cwd, timeout_secs, max_output }, options) => {
        try {
          return await run(tool, args, cwd, timeout_secs, max_output, options?.abortSignal, queuedUtilityRequest(options?.context));
        } catch (error) {
          if (isProcessAbortError(error, options?.abortSignal)) throw error;
          return { error: String(error) };
        }
      },
    })),
    run_code: queuedUtility(tool({
      description:
        "Execute Python or JavaScript using the discovered runtime and project packages, in the current workspace. Literal code is passed to the interpreter without a shell. Requires approval. Files persist; process state does not. Use returned file paths with normal file/document tools. Not an OS sandbox; no automatic package installation.",
      inputSchema: z.object({
        language: z.enum(["python", "javascript"]),
        code: z.string().max(32000),
        args: z.array(z.string().max(2000)).max(32).optional(),
        ...options,
      }),
      needsApproval: true,
      execute: async ({ language, code, args, cwd, timeout_secs }, options) => {
        try {
          return await run(
            language === "python" ? "python" : "node",
            language === "python"
              ? ["-c", code, ...(args ?? [])]
              : ["--input-type=module", "-e", code, "--", ...(args ?? [])],
            cwd,
            timeout_secs,
            undefined,
            options?.abortSignal,
            queuedUtilityRequest(options?.context),
          );
        } catch (error) {
          if (isProcessAbortError(error, options?.abortSignal)) throw error;
          return { error: String(error) };
        }
      },
    })),
    archive_list: tool({
      description:
        "Inspect a ZIP without extracting it. Built into Rust: no zip CLI needed. Bounded listing; rejects traversal, symlinks, encrypted/special entries and protected credential paths. Other archive formats can use an installed tar/7z through utility_run after approval.",
      inputSchema: z.object({
        path: z.string().max(2048),
        max_results: z.number().int().min(1).max(200).optional(),
      }),
      execute: async ({ path, max_results }) => {
        const workspace={...currentWorkspaceEnv()};
        const base=ctx.getCwd() || ctx.getWorkspaceRoot();
        try {
          const data = await invoke<{
            entries: { name: string; size: number; directory: boolean }[];
            total_entries: number;
            expanded_bytes: number;
            truncated: boolean;
          }>("archive_list", {
            path: await readable(path, base,workspace),
            workspace,
          });
          const limit = max_results ?? 30;
          return {
            ...data,
            entries: data.entries.slice(0, limit).map((entry) => ({
              ...entry,
              name: entry.name.slice(0, 240),
              name_truncated: entry.name.length > 240,
            })),
            truncated: data.truncated || data.entries.length > limit,
          };
        } catch (error) {
          return { error: String(error) };
        }
      },
    }),
    archive_create: tool({
      description:
        "Create a new ZIP from selected workspace files/folders using Rust. Preserves relative names, skips protected metadata/credential paths and links, does not overwrite existing archives. Maximum 10000 entries / 512 MiB. Requires approval. Output parent must already exist.",
      inputSchema: z.object({
        sources: z.array(z.string().max(2048)).min(1).max(100),
        destination: z.string().max(2048),
      }),
      needsApproval: true,
      execute: async ({ sources, destination }) => {
        const workspace={...currentWorkspaceEnv()};
        const base=ctx.getCwd() || ctx.getWorkspaceRoot();
        try {
          return await invoke("archive_create", {
            sources: await Promise.all(
              sources.map((source) => readable(source, base,workspace)),
            ),
            destination: await writable(destination, base,workspace),
            workspace,
          });
        } catch (error) {
          return { error: String(error) };
        }
      },
    }),
    archive_extract: tool({
      description:
        "Extract a ZIP into a NEW workspace directory using staging. Rejects path traversal, symlinks/special files, case aliases, credential paths and oversized data. Never overwrites an existing destination and never executes archive contents. Maximum 10000 entries / 512 MiB expanded. Requires approval; output parent must exist.",
      inputSchema: z.object({
        path: z.string().max(2048),
        destination: z.string().max(2048),
      }),
      needsApproval: true,
      execute: async ({ path, destination }) => {
        const workspace={...currentWorkspaceEnv()};
        const base=ctx.getCwd() || ctx.getWorkspaceRoot();
        try {
          return await invoke("archive_extract", {
            path: await readable(path, base,workspace),
            destination: await writable(destination, base,workspace),
            workspace,
          });
        } catch (error) {
          return { error: String(error) };
        }
      },
    }),
  };
}
