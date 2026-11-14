import { getLocale, translate, type Locale } from "./localization";
export type OwnedError = { source: "jarvis"; message: string; values: (string | number | boolean | null)[] };
export type DisplayError = string | OwnedError;
export function isOwnedError(value: unknown): value is OwnedError {
  if (!value || typeof value !== "object") return false;
  const error = value as Partial<OwnedError>;
  return error.source === "jarvis" && typeof error.message === "string" && error.message.length <= 4096
    && Array.isArray(error.values) && error.values.length <= 16
    && error.values.every(item => item === null || typeof item === "string" || typeof item === "boolean" || (typeof item === "number" && Number.isFinite(item)));
}
export function displayError(value: unknown): DisplayError {
  return isOwnedError(value) ? value : String(value);
}
export function errorText(value: DisplayError, locale: Locale = getLocale()): string {
  return typeof value === "string" ? value : translate(locale, value.message, ...value.values);
}
