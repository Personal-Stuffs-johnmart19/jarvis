import { useEffect, useState } from "react";
import { useLocale } from "../../../../src/i18n";
import { t } from "../../../../src/localization";
import { displayError, errorText, type DisplayError } from "../../../../src/displayError";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

export function CustomInstructionsEditor({ value, save }: {
  value: string;
  save: (value: string) => Promise<void>;
}) {
  useLocale();
  const [state, setState] = useState({ saved: value, draft: value });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<DisplayError>("");
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    setState(current => ({
      saved: value,
      draft: current.draft === current.saved ? value : current.draft,
    }));
  }, [value]);
  const persist = async () => {
    const submitted = state.draft;
    setBusy(true);
    setError("");
    setSaved(false);
    try {
      await save(submitted);
      setState(current => ({ ...current, saved: submitted }));
      setSaved(true);
    } catch (error) {
      setError(displayError(error));
    } finally {
      setBusy(false);
    }
  };
  return <div className="flex flex-col gap-2" data-custom-instructions>
    <div className="flex items-center justify-between gap-2">
      <label htmlFor="custom-instructions" className="text-[11px] font-medium tracking-tight text-muted-foreground">{t("Custom instructions")}</label>
      <Button size="xs" disabled={busy || state.draft === state.saved} onClick={() => void persist()}>{t("Save")}</Button>
    </div>
    <Textarea id="custom-instructions" value={state.draft}
      onChange={event => { setState(current => ({ ...current, draft: event.target.value })); setSaved(false); setError(""); }}
      placeholder={t("e.g. Always reply in concise bullet points. Prefer pnpm over npm. My machine is an M-series Mac.")}
      className="min-h-[100px] resize-y bg-card/60 font-sans text-[12px] leading-relaxed border border-border" />
    {error && <p role="alert">{errorText(error)}</p>}
    {saved && state.draft === state.saved && <p role="status">{t("Saved")}</p>}
  </div>;
}
