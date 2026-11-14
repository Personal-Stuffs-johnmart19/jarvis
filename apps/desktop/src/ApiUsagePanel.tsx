import { getLocale } from "./localization";
import { t } from "./localization";
import { useEffect, useState } from "react";
import "./api-usage.css";

type Row = {
  model: string;
  operation: string;
  attempts?: number;
  successes?: number;
  failures?: number;
  rate_limited?: number;
  reported_usage?: number;
  input_tokens?: number;
  output_tokens?: number;
  total_tokens?: number;
};
type Usage = { day_utc: string; rows: Row[]; persistence_error: boolean };
type Catalog = {
  models: {
    model: string;
    listed: boolean;
    input_token_limit?: number;
    output_token_limit?: number;
  }[];
};
type Props = {
  run: <T>(method: string, params?: unknown) => Promise<T | undefined>;
};
export function ApiUsagePanel({ run }: Props) {
  const [usage, setUsage] = useState<Usage>(),
    [catalog, setCatalog] = useState<Catalog>(),
    [busy, setBusy] = useState(false);
  const refresh = async () => {
    const data = await run<Usage>("api.usage");
    if (data) setUsage(data);
  };
  useEffect(() => {
    void refresh();
  }, []);
  const check = async () => {
    setBusy(true);
    try {
      const data = await run<Catalog>("api.catalog");
      if (data) setCatalog(data);
      await refresh();
    } finally {
      setBusy(false);
    }
  };
  const count = (value: number | undefined) =>
    value === undefined ? "—" : value.toLocaleString(getLocale());
  return (
    <section className="api-usage" aria-label={t("Gemini API Usage")}>
      <h3> {t("API Usage")}</h3>
      <p className="note">
        {t("Local SDK attempts for")}
        {usage?.day_utc || t("today")}
        {t(
          "(UTC). These are Jarvis counters, not the remaining project quota. Retries are counted separately. Tokens are shown only when Google metadata is available.",
        )}
      </p>
      <div className="row">
        <button onClick={refresh}>{t("Refresh counters")}</button>
        <button disabled={busy} onClick={check}>
          {busy ? t("Checking...") : t("Check available models")}
        </button>
      </div>
      {usage?.persistence_error && (
        <p role="status">
          {t(
            "Failed to save stats to disk; the current session is tracked in memory.",
          )}
        </p>
      )}
      <div className="api-table-wrap">
        <table>
          <thead>
            <tr>
              <th>{t("Model / Request")}</th>
              <th>{t("Attempts")}</th>
              <th>{t("Errors / 429")}</th>
              <th> {t("Input / Output")}</th>
              <th> {t("Total Tokens")}</th>
            </tr>
          </thead>
          <tbody>
            {usage?.rows.map((row) => (
              <tr key={row.model + row.operation}>
                <td>
                  {row.model}
                  <small>{row.operation}</small>
                </td>
                <td>{count(row.attempts)}</td>
                <td>
                  {count(row.failures || 0)} / {count(row.rate_limited || 0)}
                </td>
                <td>
                  {count(row.input_tokens)} / {count(row.output_tokens)}
                </td>
                <td>{count(row.total_tokens)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!usage?.rows.length && (
        <p>{t("No requests in this local stats yet.")}</p>
      )}
      <p className="note">
        {t(
          "Total RPM/TPM/RPD, pricing tier, and remaining quota are checked in Google AI Studio → Usage / Rate limits. The API key does not provide an accurate project balance. Other apps' counters are not included here; server daily limits reset at Pacific time.",
        )}
      </p>
      {catalog && (
        <>
          <h4>{t("Models visible to the current key")}</h4>
          <p className="note">
            {" "}
            {t(
              "Presence in the list does not confirm a free tier or sufficient quota.",
            )}
          </p>
          <div className="api-table-wrap">
            <table>
              <thead>
                <tr>
                  <th>{t("Model")}</th>
                  <th> {t("Available in list")}</th>
                  <th>{t("Context / Output")}</th>
                </tr>
              </thead>
              <tbody>
                {catalog.models.map((item) => (
                  <tr key={item.model}>
                    <td>{item.model}</td>
                    <td>{item.listed ? t("Yes") : t("Not found")}</td>
                    <td>
                      {count(item.input_token_limit)} /{" "}
                      {count(item.output_token_limit)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}
