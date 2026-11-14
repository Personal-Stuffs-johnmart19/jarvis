import { useEffect, useRef, useState } from "react";
import { t } from "./localization";
import type { SettingsRun } from "./settingsFeedback";
import { browserSelection, type BrowserProfile, type BrowserPreferences } from "./browserSelection";

type Props = {
  preferences?: BrowserPreferences;
  run: SettingsRun;
  apply: (method: string, params?: unknown) => Promise<any>;
  refresh: () => Promise<void>;
};

export function BrowserSettings({ preferences, run, apply, refresh }: Props) {
  const [selection, setSelection] = useState<BrowserPreferences>(preferences ?? { mode: "current", profile: "" });
  const [profiles, setProfiles] = useState<BrowserProfile[]>([]);
  const [current, setCurrent] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [token, setToken] = useState("");
  const edited = useRef(false);
  useEffect(() => {
    if (!edited.current && preferences) setSelection(preferences);
  }, [preferences?.mode, preferences?.profile]);
  useEffect(() => {
    let active = true;
    run<{ profiles: BrowserProfile[]; current: string }>("browser.profiles").then(data => {
      if (active && data) {
        setProfiles(data.profiles);
        setCurrent(data.current);
        setLoaded(true);
      }
    });
    return () => { active = false; };
  }, []);
  const resolved = browserSelection(selection, profiles, current);
  const valid = loaded && profiles.some(item => item.id === resolved.profile);
  const change = (next: BrowserPreferences) => {
    edited.current = true;
    setToken("");
    setSelection(next);
  };
  return <>
    <p>{t("Regular Chrome connects via the installed Playwright Extension. Profile settings and DPAPI tokens are saved.")}</p>
    <label>{t("Profile Mode")}
      <select value={selection.mode} onChange={event => change({ mode: event.target.value, profile: resolved.profile })}>
        <option value="current">{t("Current Chrome Profile")}</option>
        <option value="specific">{t("Specific Profile")}</option>
        <option value="new">{t("Separate Persistent Profile")}</option>
      </select>
    </label>
    <label>{t("Profile")}
      <select value={valid ? resolved.profile : ""} disabled={!loaded || selection.mode === "new"} onChange={event => change({ mode: "specific", profile: event.target.value })}>
        {!valid && <option value="">{t("Select an existing profile from the list.")}</option>}
        {profiles.map(item => <option key={item.id} value={item.id}>{item.name} {item.email} {item.token_configured ? t("· token saved") : ""}</option>)}
      </select>
    </label>
    <button className="primary" disabled={!loaded || (selection.mode !== "new" && !valid)} onClick={async () => {
      if (await apply("browser.save", { mode: selection.mode, profile: resolved.profile, engine: "chrome" })) await refresh();
    }}>{t("Save Profile")}</button>
    {selection.mode !== "new" && <>
      <label>{t("Playwright Extension Token")}
        <input type="password" value={token} autoComplete="off" spellCheck={false} disabled={!valid} onChange={event => { edited.current = true; setToken(event.target.value); }} placeholder={t("Paste the token or PLAYWRIGHT_MCP_EXTENSION_TOKEN=… string")} />
      </label>
      <button disabled={!valid || !token.trim()} onClick={async () => {
        const profile = resolved.profile;
        if (await apply("chrome.token", { profile, token })) {
          setToken("");
          setProfiles(old => old.map(item => item.id === profile ? { ...item, token_configured: true } : item));
        }
      }}>{t("Save token for this profile")}</button>
    </>}
  </>;
}
