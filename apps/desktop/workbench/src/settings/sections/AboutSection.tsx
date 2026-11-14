import { t } from "../../../../src/localization";
import { version } from "../../../../package.json";
import { SectionHeader } from "../components/SectionHeader";

export function AboutSection() {
  return (
    <div className="flex flex-col gap-6">
      <SectionHeader
        title={t("About Jarvis")}
        description={t("Voice assistant and AI workspace")}
      />
      <div className="rounded-xl border border-border/60 bg-card/60 p-5">
        <h2 className="text-lg font-semibold">{t("Jarvis")}</h2>
        <p className="text-muted-foreground">
          {t("Version")} {version}
        </p>
      </div>
      <dl className="grid grid-cols-[100px_1fr] gap-3 text-sm select-text">
        <dt>{t("Source")}</dt>
        <dd className="break-all" translate="no">github.com/Personal-Stuffs-johnmart19/jarvis</dd>
        <dt>{t("Licenses")}</dt>
        <dd>{t("Third-party license notices are included with the Jarvis package.")}</dd>
      </dl>
      <p className="text-sm text-muted-foreground">
        {t(
          "Update Jarvis through its regular installation workflow. Workbench components are updated together with Jarvis.",
        )}{" "}
      </p>
    </div>
  );
}
