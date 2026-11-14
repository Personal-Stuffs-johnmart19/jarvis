/** GPT-OSS supports low/medium/high, never a disabled reasoning mode.
 * Keep persisted "off" usable by selecting its least expensive supported effort.
 */
export function lmstudioReasoningEffort(modelId: string, setting: string): string | undefined {
  if (setting === "default") return undefined;
  const effort = setting === "off" ? "none" : setting === "on" ? "high" : setting;
  return effort === "none" && /^(?:openai\/)?gpt-oss-(?:20b|120b)(?:[-_:][a-z0-9._:-]+)?$/i.test(modelId.trim())
    ? "low" : effort;
}
