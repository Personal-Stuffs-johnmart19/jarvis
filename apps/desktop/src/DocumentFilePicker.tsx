import { invoke } from "@tauri-apps/api/core";
import { useRef, useState } from "react";
import { t } from "./localization";
import { displayError, errorText, type DisplayError } from "./displayError";
import { useLocale } from "./i18n";

export function DocumentFilePicker({
  accept,
  disabled,
  onSelect,
}: {
  accept: string;
  disabled?: boolean;
  onSelect: (path: string, name: string) => void;
}) {
  useLocale();
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<DisplayError>("");
  const choose = async (file: File | undefined) => {
    if (!file) return;
    setBusy(true);
    setError("");
    try {
      if (!file.size || file.size > 12 * 1024 * 1024)
        throw { source: "jarvis", message: "File must contain 1 byte to 12 MiB.", values: [] };
      const data = await new Promise<string>((resolve, reject) => {
        const r = new FileReader();
        r.onload = () => resolve(String(r.result).split(",")[1]);
        r.onerror = () => reject(r.error);
        r.readAsDataURL(file);
      });
      const result = await invoke<{ path: string }>("import_attachment", {
        name: file.name,
        data,
      });
      await invoke("backend", {
        method: "documents.import",
        params: { path: result.path, name: file.name },
      });
      onSelect(result.path, file.name);
    } catch (e) {
      setError(displayError(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="document-file-picker">
      <input
        ref={input}
        type="file"
        accept={accept}
        hidden
        aria-label={t("Choose document file")}
        onChange={(e) => {
          void choose(e.target.files?.[0]);
          e.target.value = "";
        }}
      />
      <button
        type="button"
        disabled={disabled || busy}
        onClick={() => input.current?.click()}
      >
        {t(busy ? "Importing file…" : "Open file…")}
      </button>
      {error && <p role="alert">{errorText(error)}</p>}
    </div>
  );
}
