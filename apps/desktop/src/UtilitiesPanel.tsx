import { displayError, errorText, type DisplayError } from "./displayError";
import { useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { t } from "./localization";
import { useLocale } from "./i18n";
import { presentUtility, utilityMatchesQuery } from "./utilityPresentation";
type Utility = {
  id: string;
  category: string;
  description: string;
  available: boolean;
  path: string | null;
  source: string;
  direct: boolean;
  builtin: boolean;
  install_package: string | null;
  installer_available: boolean;
};
type InstallResult = { success: boolean; cancelled: boolean; exit_code: number | null; stdout: string; stderr: string };
export function UtilitiesPanel() {
  const locale = useLocale();
  const [items, setItems] = useState<Utility[]>([]),
    [query, setQuery] = useState(""),
    [busy, setBusy] = useState(true),
    [error, setError] = useState<DisplayError>("");
  const [selected, setSelected] = useState<Utility | null>(null);
  const installForm = useRef<HTMLFieldSetElement>(null);
  useEffect(() => {
    if (selected) {
      installForm.current?.scrollIntoView({ block: "center", behavior: "smooth" });
      installForm.current?.focus({ preventScroll: true });
    }
  }, [selected]);
  const [scope, setScope] = useState("user");
  const [agreements, setAgreements] = useState(false);
  const [installing, setInstalling] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const activeRequest = useRef<string | null>(null);
  const [installLog, setInstallLog] = useState<InstallResult | null>(null);
  const cancelInstall = async () => {
    const requestId = activeRequest.current;
    if (!requestId || cancelling) return;
    setCancelling(true);
    try {
      await invoke("utility_install_cancel", { requestId });
    } catch (e) {
      setError(displayError(e));
      setCancelling(false);
    }
  };
  const install = async () => {
    if (!selected?.install_package || !agreements || activeRequest.current) return;
    const requestId = crypto.randomUUID();
    activeRequest.current = requestId;
    setInstalling(true);
    setError("");
    setInstallLog(null);
    try {
      const result = await invoke<{
        success: boolean;
        available: boolean;
        exit_code: number | null;
        timed_out: boolean;
        cancelled: boolean;
        stdout: string;
        stderr: string;
      }>("utility_install", {
        tool: selected.id,
        expectedPackage: selected.install_package,
        scope,
        agreements,
        requestId,
      });
      setInstallLog(result);
      await refresh();
      if (result.cancelled)
        setError({ source: "jarvis", message: "WinGet was cancelled. Detached Windows installer services may still be running; inspect Windows installation status before retrying.", values: [] });
      else if (result.timed_out)
        setError({ source: "jarvis", message: "WinGet timed out. An installer may still be running; inspect Windows installation status before trying again.", values: [] });
      else if (!result.available)
        setError({ source: "jarvis", message: "The executable is not detected yet. Review the installer output and restart Jarvis if the package changed PATH. No automatic retry is performed.", values: [] });
      if (result.success) setSelected(null);
    } catch (e) {
      setError(displayError(e));
    } finally {
      activeRequest.current = null;
      setCancelling(false);
      setInstalling(false);
    }
  };
  const refresh = async () => {
    setBusy(true);
    setError("");
    try {
      setItems(await invoke<Utility[]>("utility_inventory"));
    } catch (e) {
      setError(displayError(e));
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => {
    void refresh();
  }, []);
  const visible = items.filter((item) => utilityMatchesQuery(item, query));
  return (
    <section
      className="settings-content utility-panel"
      aria-label={t("Utilities and runtimes")}
    >
      <p>
        {t(
          "One toolchain for chat, code and terminals. Built-in search and ZIP tools work without separate rg/zip executables. Other utilities use installed programs; missing tools are not downloaded automatically.",
        )}
      </p>
      <p>
        {t(
          "Code and CLI execution require approval and run with your normal user permissions, not in a sandbox. WSL utilities run through the selected WSL terminal.",
        )}
      </p>
      <div className="row">
        <input
          type="search"
          aria-label={t("Search utilities")}
          placeholder={t("Search utilities")}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <button disabled={busy} onClick={() => void refresh()}>
          {t("Refresh")}
        </button>
      </div>
      <p role="status">
        {busy
          ? t("Loading…")
          : `${items.filter((item) => item.available).length} / ${items.length} ${t("available")}`}
      </p>
      {error && <p role="alert">{errorText(error, locale)}</p>}
      {selected && (
        <fieldset ref={installForm} tabIndex={-1} className="utility-install" disabled={installing}>
          <legend>
            {t("Install utility")}: {selected.id}
          </legend>
          <p>{t("WinGet · ")}{selected.install_package}</p>
          <p>
            {t(
              "Downloads the selected package and its dependencies from the WinGet community source. No bulk installation. Machine scope may require Windows administrator approval. Installation can take up to 15 minutes.",
            )}
          </p>
          <label>
            {t("Installation scope")}
            <select
              aria-label={t("Installation scope")}
              value={scope}
              onChange={(e) => setScope(e.target.value)}
            >
              <option value="user">{t("Current user")}</option>
              <option value="machine">{t("All users (administrator)")}</option>
            </select>
          </label>
          <p>
            {t(
              "Some packages do not support current-user installation. Select machine scope only if needed; Jarvis does not retry with higher privileges.",
            )}
          </p>
          <label>
            <input
              type="checkbox"
              checked={agreements}
              onChange={(e) => setAgreements(e.target.checked)}
            />
            {t(
              "I accept the package and WinGet source agreements for this installation.",
            )}
          </label>
          <div className="row">
            <button
              disabled={!agreements || installing}
              onClick={() => void install()}
            >
              {t(installing ? "Installing…" : "Install package")}
            </button>
            <button onClick={() => setSelected(null)}>{t("Cancel")}</button>
          </div>
        </fieldset>
      )}
      {installing && <div className="row">
        <p role="status">{t(cancelling ? "Cancelling…" : "Installing…")}</p>
        <button disabled={cancelling} onClick={() => void cancelInstall()}>{t("Cancel")}</button>
      </div>}
      {installLog && (
        <details className="utility-install-log" open>
          <summary>{t("Installation output")}</summary>
          <pre>{`${t(installLog.cancelled ? "Package installation cancelled" : installLog.success ? "Package installation completed" : "Package installation failed")} (${installLog.exit_code ?? "-"})\n${installLog.stdout}\n${installLog.stderr}`}</pre>
        </details>
      )}
      <div className="utility-grid">
        {visible.map((rawItem) => {
          const item = presentUtility(rawItem, locale);
          return (
            <article
              key={item.id}
              className="utility-card"
              data-utility={item.id}
            >
              <header>
                <strong>{item.id}</strong>
                <span>
                  {t(
                    !item.path && item.builtin
                      ? "Built-in"
                      : !item.available
                        ? "Not installed"
                        : item.direct
                          ? "Available"
                          : "Shell only",
                  )}
                </span>
              </header>
              <p>{item.description}</p>
              <small>
                {item.category} · {item.source}
              </small>
              {item.path && <code>{item.path}</code>}
              {!item.path && item.install_package && (
                <button
                  disabled={!item.installer_available || installing}
                  onClick={() => {
                    setSelected(rawItem);
                    setAgreements(false);
                    setScope("user");
                    setInstallLog(null);
                    setError("");
                  }}
                >
                  {t("Install…")}
                </button>
              )}
              {!item.path &&
                item.install_package &&
                !item.installer_available && (
                  <small>
                    {t(
                      "Install Windows App Installer (WinGet) to enable package installation.",
                    )}
                  </small>
                )}
              {!item.available && !item.install_package && (
                <small>
                  {t(
                    "No managed installer. Configure this tool manually or use a built-in alternative.",
                  )}
                </small>
              )}
            </article>
          );
        })}
      </div>
      {!busy && !visible.length && <p>{t("No matching utilities")}</p>}
    </section>
  );
}
