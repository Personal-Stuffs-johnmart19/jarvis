import { DocumentFilePicker } from "./DocumentFilePicker";
import { t } from "./localization";
import { useEffect, useState } from "react";

type Props = {
  run: <T>(method: string, params?: unknown) => Promise<T | undefined>;
};
type Result = {
  text: string;
  backend: string;
  source: string;
  target: string;
  fallback_reason: string;
};
const languages = [
  ["ru", "Russian"],
  ["en", "English"],
  ["uk", "Ukrainian"],
  ["de", "German"],
  ["fr", "French"],
  ["es", "Spanish"],
  ["it", "Italian"],
  ["pt", "Portuguese"],
  ["zh", "Chinese"],
  ["ja", "Japanese"],
  ["ko", "Korean"],
  ["ar", "Arabic"],
  ["tr", "Turkish"],
  ["uz", "Uzbek"],
];

export function TranslationPanel({ run }: Props) {
  const [backend, setBackend] = useState("auto");
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [text, setText] = useState("");
  const [path, setPath] = useState("");
  const [fileName, setFileName] = useState("");
  const [source, setSource] = useState("auto");
  const [target, setTarget] = useState("en");
  const [result, setResult] = useState<Result>();
  useEffect(() => {
    let alive = true;
    void run<{ backend: string }>("translation.settings").then((data) => {
      if (data && alive) {
        setBackend(data.backend);
        setReady(true);
      }
    });
    return () => {
      alive = false;
    };
  }, []);
  const choose = async (value: string) => {
    setBusy(true);
    try {
      const saved = await run<{ backend: string }>("translation.settings", {
        backend: value,
      });
      if (saved) {
        setBackend(saved.backend);
        if (saved.backend === "local") {
          setSource((old) =>
            ["auto", "ru", "en"].includes(old) ? old : "auto",
          );
          setTarget((old) => (["ru", "en"].includes(old) ? old : "en"));
        }
        setResult(undefined);
      }
    } finally {
      setBusy(false);
    }
  };
  const translate = async () => {
    setBusy(true);
    setResult(undefined);
    try {
      const data = await run<Result>("translation.run", {
        text,
        path,
        source,
        target,
        backend,
      });
      if (data) setResult(data);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section
      className="api-usage settings-content document-panel"
      aria-label={t("Text and Document Translation")}
    >
      <h3>{t("Text and Document Translation")}</h3>
      <DocumentFilePicker
        accept=".txt,.md,.pdf,.docx"
        disabled={busy}
        onSelect={(path, name) => {
          setFileName(name);
          setPath(path);
          setText("");
          setResult(undefined);
        }}
      />
      {fileName && <p role="status">{fileName}</p>}
      <label>
        {" "}
        {t("Translation Mode")}
        <select
          aria-label={t("Translation Mode")}
          value={backend}
          disabled={!ready || busy}
          onChange={(e) => void choose(e.target.value)}
        >
          <option value="auto">
            {t("Gemini → local fallback on quota / error")}
          </option>
          <option value="cloud"> {t("Gemini only — shared API key")}</option>
          <option value="local">{t("Local only — RU ↔ EN, CPU")}</option>
        </select>
      </label>
      <p className="note">
        {backend === "local"
          ? t(
              "Text remains on this PC. The first translation will automatically download the Argos package for the selected direction (156–196 MB); then works offline on CPU.",
            )
          : t(
              'Text or document is sent to Google only after clicking "Translate". This is Gemini API with the current key, not a separate Google Cloud Translation. Auto mode uses local fallback on quota, missing key, or network error.',
            )}
      </p>
      <p className="note">
        {t(
          "Russian ↔ English is supported locally. Local auto-detection only distinguishes RU/EN; for another source language, select it explicitly. Windows OCR / Tesseract is used for PDF scans. Translation is returned as text; original document formatting is not preserved.",
        )}
      </p>
      <div className="row">
        <label>
          {t("Source Language")}
          <select
            aria-label={t("Source Translation Language")}
            value={source}
            disabled={busy}
            onChange={(e) => setSource(e.target.value)}
          >
            <option value="auto">{t("Detect Automatically")}</option>
            {languages.map(([code, name]) => (
              <option
                key={code}
                value={code}
                disabled={backend === "local" && !["ru", "en"].includes(code)}
              >
                {t(name)}
              </option>
            ))}
          </select>
        </label>
        <label>
          {t("Target Language")}{" "}
          <select
            aria-label={t("Target Language")}
            value={target}
            disabled={busy}
            onChange={(e) => setTarget(e.target.value)}
          >
            {languages.map(([code, name]) => (
              <option
                key={code}
                value={code}
                disabled={backend === "local" && !["ru", "en"].includes(code)}
              >
                {t(name)}
              </option>
            ))}
          </select>
        </label>
      </div>
      <label>
        {t("Text")}
        <textarea
          aria-label={t("Text to Translate")}
          value={text}
          maxLength={24000}
          disabled={busy}
          onChange={(e) => setText(e.target.value)}
        />
      </label>
      <details>
        <summary>{t("Advanced: file path")}</summary>
        <label>
          {t("Or Document Path")}
          <input
            aria-label={t("Document to Translate")}
            value={path}
            maxLength={2048}
            disabled={busy}
            onChange={(e) => {
              setPath(e.target.value);
              setFileName("");
            }}
            placeholder={t("Choose a file or enter its path")}
          />
        </label>
      </details>
      <p className="note">
        {t(
          "TXT/Markdown UTF-8, DOCX or PDF; up to 12 MiB and 24,000 local text characters, PDF up to 6 pages. Specify text or file.",
        )}
      </p>
      <button
        disabled={
          !ready ||
          busy ||
          (!text.trim() && !path.trim()) ||
          (!!text.trim() && !!path.trim())
        }
        onClick={() => void translate()}
      >
        {busy ? t("Translating / preparing local model…") : t("Translate")}
      </button>
      {result && (
        <>
          <p role="status">
            {t("Translation completed:")}{" "}
            {result.backend === "cloud" ? "Gemini" : t("locally, CPU")},{" "}
            {result.source} → {result.target}.
            {result.fallback_reason &&
              " Local fallback: " + result.fallback_reason}
          </p>
          <label>
            {" "}
            {t("Result")}
            <textarea
              aria-label={t("Translation Result")}
              readOnly
              value={result.text}
              rows={8}
            />
          </label>
        </>
      )}
    </section>
  );
}
