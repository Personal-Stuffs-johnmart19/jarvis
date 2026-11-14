import { generateText, Output, type LanguageModel } from "ai";
import type { HelperTextDependencies } from "./helperText";

/** Structured JSON is a provider wire contract, not only a prompt instruction. */
export const generateHelperText: HelperTextDependencies["generate"] = ({ jsonMode, ...options }) =>
  generateText({ ...options, model: options.model as LanguageModel,
    ...(jsonMode ? { output: Output.json() } : {}) });
