import samples from "../../../local_mode/locales/speech-previews.json" with { type: "json" };

/** Synthesis language is independent of interface language. Custom text is never translated. */
export function speechPreview(language: string | undefined, custom?: string): string {
  if (custom !== undefined) return custom;
  return language && Object.hasOwn(samples, language)
    ? samples[language as keyof typeof samples]
    : "";
}
