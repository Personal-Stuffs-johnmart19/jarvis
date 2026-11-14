import { displayError, type DisplayError, type OwnedError } from "./displayError";

export function settingsMessage(message: string): OwnedError {
  return { source: "jarvis", message, values: [] };
}

export type SettingsRun = <T = any>(method: string, params?: unknown, rethrow?: boolean) => Promise<T | undefined>;

// Keep message provenance until render so locale changes never translate raw diagnostics.
export async function applySettingsAction(
  run: SettingsRun,
  method: string,
  params: unknown,
  setStatus: (status: DisplayError) => void,
) {
  setStatus(settingsMessage("Running..."));
  try {
    const result = await run<any>(method, params, true);
    if (result === undefined || result === null) {
      setStatus(settingsMessage("Execution failed. See error message for details."));
      return undefined;
    }
    const failed = result.ok === false;
    const detail = failed ? result.reply || result.error || result.message : result.message || result.reply;
    setStatus(detail ? displayError(detail) : settingsMessage(failed ? "Action failed." : "Done"));
    return failed ? undefined : result;
  } catch (error) {
    setStatus(displayError(error instanceof Error ? error.message : error));
    return undefined;
  }
}
