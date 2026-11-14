import backendEn from "../../../local_mode/locales/en.json" with { type: "json" };
import backendRu from "../../../local_mode/locales/ru.json" with { type: "json" };
import enPlurals from "./locales/en.plurals.json" with { type: "json" };
import ruPlurals from "./locales/ru.plurals.json" with { type: "json" };
import en from "./locales/en.json" with { type: "json" };
import ru from "./locales/ru.json" with { type: "json" };

export const localeDefinitions = {
  en: { label: "English", messages: { ...backendEn, ...en }, plurals: enPlurals },
  ru: { label: "Russian", messages: { ...backendRu, ...ru }, plurals: ruPlurals },
} as const;
export type Locale = keyof typeof localeDefinitions;
export const supportedLocales = Object.keys(localeDefinitions) as Locale[];
export function isLocale(value: unknown): value is Locale {
  return typeof value === "string" && Object.hasOwn(localeDefinitions, value);
}
export const localeKey = "jarvis.ui.language";
export function getLocale(): Locale {
  try {
    const saved = globalThis.localStorage?.getItem(localeKey);
    return isLocale(saved) ? saved : "en";
  } catch {
    return "en";
  }
}
export function translate(
  locale: Locale,
  message: string,
  ...values: unknown[]
): string {
  const catalog: Record<string, string> = localeDefinitions[locale]?.messages ?? en;
  const text = Object.hasOwn(catalog, message)
    ? catalog[message]
    : Object.hasOwn(en, message)
      ? en[message as keyof typeof en]
      : message;
  return text.replace(/\{(\d+)\}/g, (placeholder, i: string) =>
    Number(i) < values.length ? String(values[Number(i)]) : placeholder,
  );
}
export function t(message: string, ...values: unknown[]): string {
  return translate(getLocale(), message, ...values);
}
export function setLocale(locale: Locale) {
  if (!isLocale(locale))
    throw new Error("Unsupported interface language");
  localStorage.setItem(localeKey, locale);
  window.dispatchEvent(new Event("jarvis:language"));
}

const pluralRules = new Map<Locale, Intl.PluralRules>();
type PluralForms = Partial<Record<Intl.LDMLPluralRule, string>> & { other: string };
export function translateCount(locale: Locale, message: string, count: number): string {
  const selected = isLocale(locale) ? locale : "en";
  const catalog: Record<string, PluralForms> = localeDefinitions[selected].plurals;
  const english: Record<string, PluralForms> = enPlurals;
  const ruleLocale = Object.hasOwn(catalog, message) ? selected : "en";
  const forms = Object.hasOwn(catalog, message) ? catalog[message]
    : Object.hasOwn(english, message) ? english[message] : undefined;
  if (!forms) return translate(selected, message, count);
  let rules = pluralRules.get(ruleLocale);
  if (!rules) { rules = new Intl.PluralRules(ruleLocale); pluralRules.set(ruleLocale, rules); }
  return (forms[rules.select(count)] ?? forms.other).replace(/\{0\}/g, String(count));
}
export function countMessage(message: string, count: number): string {
  return translateCount(getLocale(), message, count);
}
