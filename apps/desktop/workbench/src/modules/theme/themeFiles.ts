import { IS_WINDOWS } from "@/lib/platform";
import { findThemeFile, removeThemeFiles, probeThemeFile } from "./themeFileCompatibility";
import { LOCAL_WORKSPACE } from "@/modules/workspace";
import { invoke } from "@/host/core";
import { emit, listen, type UnlistenFn } from "@/host/event";
import { appConfigDir, join } from "@/host/path";
import type { Theme } from "./types";
import { validateTheme, type ValidationResult } from "./validateTheme";

const THEME_FILE_EXT = ".jarvis-theme";
const THEME_EDIT_EVENT = "terax://theme-edit";

export type ThemeEditRequest =
  | { action: "create" }
  | { action: "edit"; id: string };

export function isThemeFilePath(path: string): boolean {
  return path.toLowerCase().endsWith(THEME_FILE_EXT) || path.toLowerCase().endsWith(".terax-theme");
}

async function themesDir(): Promise<string> {
  return join(await appConfigDir(), "themes");
}

async function themePaths(id: string): Promise<[string, string]> {
  const dir = await themesDir();
  return [await join(dir, `${id}${THEME_FILE_EXT}`), await join(dir, `${id}.terax-theme`)];
}

const themeFileExists = (path: string) => probeThemeFile(() => invoke("fs_stat", { path, workspace: LOCAL_WORKSPACE }), IS_WINDOWS);

export async function themeFilePath(id: string): Promise<string> {
  return findThemeFile(await themePaths(id), themeFileExists);
}

export async function writeThemeFile(theme: Theme): Promise<string> {
  const dir = await themesDir();
  const ws = LOCAL_WORKSPACE;
  const dirExists = await invoke("fs_stat", { path: dir, workspace: ws })
    .then(() => true)
    .catch(() => false);
  if (!dirExists) {
    await invoke("fs_create_dir", { path: dir, workspace: ws });
  }
  const path = await themeFilePath(theme.id);
  await invoke("fs_write_file", {
    path,
    content: JSON.stringify(theme, null, 2),
    workspace: ws,
    source: "theme",
  });
  return path;
}

export async function deleteThemeFile(id: string): Promise<void> {
  await removeThemeFiles(await themePaths(id), themeFileExists,
    path => invoke("fs_delete", { path, workspace: LOCAL_WORKSPACE }));
}

export function parseThemeFile(text: string): ValidationResult {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch (e) {
    return {
      ok: false,
      error: e instanceof Error ? e.message : "invalid JSON",
    };
  }
  return validateTheme(parsed);
}

export function starterTheme(): Theme {
  const id = `my-theme-${crypto.randomUUID().slice(0, 8)}`;
  return {
    id,
    name: "My Theme",
    description: "Custom theme.",
    variants: {
      dark: {
        colors: {
          background: "#0d0d10",
          foreground: "#e8e8ea",
          card: "#15151a",
          cardForeground: "#e8e8ea",
          popover: "#15151a",
          popoverForeground: "#e8e8ea",
          primary: "#7dd3fc",
          primaryForeground: "#0d0d10",
          muted: "#1c1c22",
          mutedForeground: "#a0a0a8",
          accent: "#1c1c22",
          accentForeground: "#e8e8ea",
          border: "rgba(255,255,255,0.08)",
          input: "rgba(255,255,255,0.12)",
          ring: "#7dd3fc",
          sidebar: "#0a0a0d",
          sidebarForeground: "#e8e8ea",
          sidebarPrimary: "#7dd3fc",
          sidebarAccent: "#1c1c22",
          sidebarBorder: "rgba(255,255,255,0.08)",
          sidebarRing: "#7dd3fc",
        },
        terminal: {
          background: "#0d0d10",
          foreground: "#e8e8ea",
          cursor: "#e8e8ea",
          cursorAccent: "#0d0d10",
          selection: "rgba(125,211,252,0.22)",
          fontFamily: "JetBrains Mono",
          fontWeight: "normal",
          fontSize: 14,
        },
      },
    },
  };
}

function emitThemeEdit(req: ThemeEditRequest): Promise<void> {
  return emit(THEME_EDIT_EVENT, req);
}

/**
 * Hands a theme-edit request to the main window and then closes the caller
 * (the Settings window). Closing, not hiding, releases the second WebContent
 * process; Settings is rebuilt on demand by `open_settings_window`. The emit
 * is awaited first so the request is on the event bus before the window goes
 * away, and a failed emit leaves Settings open instead of dropping the edit.
 */
export async function handOffThemeEdit(
  req: ThemeEditRequest,
  closeWindow: () => Promise<void>,
  emitRequest: (req: ThemeEditRequest) => Promise<void> = emitThemeEdit,
): Promise<boolean> {
  try {
    await emitRequest(req);
  } catch (error) {
    console.error("[terax] theme edit request failed:", error);
    return false;
  }
  try {
    await closeWindow();
  } catch (error) {
    console.error("[terax] could not close settings window:", error);
  }
  return true;
}

export function onThemeEdit(
  cb: (req: ThemeEditRequest) => void,
): Promise<UnlistenFn> {
  return listen<ThemeEditRequest>(THEME_EDIT_EVENT, (e) => cb(e.payload));
}
