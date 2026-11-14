import type { Theme, ThemeColors, ThemeVariant, TerminalPalette } from "./types";
import type { OwnedError } from "../../../../src/displayError";

export type ValidationResult =
  | { ok: true; theme: Theme }
  | { ok: false; error: string; issue?: OwnedError };

type ValidationIssue = { error: string; issue: OwnedError };
function invalid(message: string, ...values: OwnedError["values"]): ValidationIssue {
  return {
    error: message.replace(/\{(\d+)\}/g, (token, index) => Number(index) < values.length ? String(values[Number(index)]) : token),
    issue: { source: "jarvis", message, values },
  };
}
function isIssue(value: object): value is ValidationIssue {
  return "issue" in value;
}

const COLOR_KEYS: readonly (keyof ThemeColors)[] = [
  "background", "foreground",
  "card", "cardForeground",
  "popover", "popoverForeground",
  "primary", "primaryForeground",
  "secondary", "secondaryForeground",
  "muted", "mutedForeground",
  "accent", "accentForeground",
  "destructive",
  "border", "input", "ring",
  "sidebar", "sidebarForeground",
  "sidebarPrimary", "sidebarPrimaryForeground",
  "sidebarAccent", "sidebarAccentForeground",
  "sidebarBorder", "sidebarRing",
  "radius",
];

const ID_RE = /^[a-z0-9][a-z0-9-]{1,63}$/;
const TERMINAL_FONT_WEIGHTS = new Set([
  "normal",
  "bold",
  "100",
  "200",
  "300",
  "400",
  "500",
  "600",
  "700",
  "800",
  "900",
]);
const TERMINAL_FONT_SIZE_MIN = 8;
const TERMINAL_FONT_SIZE_MAX = 32;

function isObj(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function isStr(v: unknown): v is string {
  return typeof v === "string";
}

function parseColors(raw: unknown, path: string): ThemeColors | ValidationIssue {
  if (raw === undefined) return {};
  if (!isObj(raw)) return invalid("{0} must be an object", path);
  const out: ThemeColors = {};
  for (const k of Object.keys(raw)) {
    if (!(COLOR_KEYS as string[]).includes(k)) {
      return invalid("{0} is not a recognized color key", `${path}.${k}`);
    }
    const v = raw[k];
    if (!isStr(v) || v.length === 0) return invalid("{0} must be a non-empty string", `${path}.${k}`);
    out[k as keyof ThemeColors] = v;
  }
  return out;
}

function parseTerminal(raw: unknown, path: string): TerminalPalette | ValidationIssue {
  if (raw === undefined) return {};
  if (!isObj(raw)) return invalid("{0} must be an object", path);
  const out: TerminalPalette = {};
  if (raw.background !== undefined) {
    if (!isStr(raw.background)) return invalid("{0} must be a string", `${path}.background`);
    out.background = raw.background;
  }
  if (raw.foreground !== undefined) {
    if (!isStr(raw.foreground)) return invalid("{0} must be a string", `${path}.foreground`);
    out.foreground = raw.foreground;
  }
  if (raw.cursor !== undefined) {
    if (!isStr(raw.cursor)) return invalid("{0} must be a string", `${path}.cursor`);
    out.cursor = raw.cursor;
  }
  if (raw.cursorAccent !== undefined) {
    if (!isStr(raw.cursorAccent)) return invalid("{0} must be a string", `${path}.cursorAccent`);
    out.cursorAccent = raw.cursorAccent;
  }
  if (raw.selection !== undefined) {
    if (!isStr(raw.selection)) return invalid("{0} must be a string", `${path}.selection`);
    out.selection = raw.selection;
  }
  if (raw.fontFamily !== undefined) {
    if (!isStr(raw.fontFamily) || raw.fontFamily.trim().length === 0) {
      return invalid("{0} must be a non-empty string", `${path}.fontFamily`);
    }
    out.fontFamily = raw.fontFamily.trim();
  }
  if (raw.fontWeight !== undefined) {
    if (!isStr(raw.fontWeight) || !TERMINAL_FONT_WEIGHTS.has(raw.fontWeight)) {
      return invalid("{0} must be normal, bold, or a weight from 100 to 900", `${path}.fontWeight`);
    }
    out.fontWeight = raw.fontWeight;
  }
  if (raw.fontSize !== undefined) {
    if (
      typeof raw.fontSize !== "number" ||
      !Number.isInteger(raw.fontSize) ||
      raw.fontSize < TERMINAL_FONT_SIZE_MIN ||
      raw.fontSize > TERMINAL_FONT_SIZE_MAX
    ) {
      return invalid("{0} must be an integer from {1} to {2}", `${path}.fontSize`, TERMINAL_FONT_SIZE_MIN, TERMINAL_FONT_SIZE_MAX);
    }
    out.fontSize = raw.fontSize;
  }
  if (raw.ansi !== undefined) {
    if (!Array.isArray(raw.ansi) || raw.ansi.length !== 16) {
      return invalid("{0} must be an array of 16 strings", `${path}.ansi`);
    }
    for (let i = 0; i < 16; i++) {
      if (!isStr(raw.ansi[i])) return invalid("{0} must be a string", `${path}.ansi[${i}]`);
    }
    out.ansi = raw.ansi as unknown as TerminalPalette["ansi"];
  }
  return out;
}

function parseVariant(raw: unknown, path: string): ThemeVariant | ValidationIssue {
  if (!isObj(raw)) return invalid("{0} must be an object", path);
  const colors = parseColors(raw.colors, `${path}.colors`);
  if (isIssue(colors)) return colors;
  const terminal = parseTerminal(raw.terminal, `${path}.terminal`);
  if (isIssue(terminal)) return terminal;
  return { colors, terminal };
}

export function validateTheme(raw: unknown): ValidationResult {
  if (!isObj(raw)) return { ok: false, ...invalid("Theme must be a JSON object") };
  if (!isStr(raw.id) || !ID_RE.test(raw.id)) {
    return { ok: false, ...invalid("id must be a kebab-case string (a-z, 0-9, -)") };
  }
  if (!isStr(raw.name) || raw.name.trim().length === 0) {
    return { ok: false, ...invalid("name must be a non-empty string") };
  }
  if (!isObj(raw.variants)) return { ok: false, ...invalid("variants must be an object") };
  const variants: Theme["variants"] = {};
  if (raw.variants.light !== undefined) {
    const v = parseVariant(raw.variants.light, "variants.light");
    if (isIssue(v)) return { ok: false, ...v };
    variants.light = v;
  }
  if (raw.variants.dark !== undefined) {
    const v = parseVariant(raw.variants.dark, "variants.dark");
    if (isIssue(v)) return { ok: false, ...v };
    variants.dark = v;
  }
  if (!variants.light && !variants.dark) {
    return { ok: false, ...invalid("variants must contain at least one of: light, dark") };
  }
  const theme: Theme = {
    id: raw.id,
    name: raw.name.trim(),
    variants,
  };
  if (isStr(raw.author)) theme.author = raw.author;
  if (isStr(raw.description)) theme.description = raw.description;
  if (isObj(raw.editorTheme)) {
    const et: Theme["editorTheme"] = {};
    if (isStr(raw.editorTheme.light)) et.light = raw.editorTheme.light;
    if (isStr(raw.editorTheme.dark)) et.dark = raw.editorTheme.dark;
    if (et.light || et.dark) theme.editorTheme = et;
  }
  return { ok: true, theme };
}
