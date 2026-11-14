import { createRoot } from "react-dom/client";
import { useState } from "react";
import { SidebarRail } from "../workbench/src/modules/sidebar/SidebarRail";
import { useLocale } from "../src/i18n";
import { setLocale } from "../src/localization";
import { displayError, errorText, type DisplayError } from "../src/displayError";
import type { SidebarViewId } from "../workbench/src/modules/sidebar/types";

function Fixture() {
  const locale = useLocale();
  const [view, setView] = useState<SidebarViewId>("explorer");
  const [error, setError] = useState<DisplayError>("");
  Object.assign(window, { __localePolishQA: { setLocale, backendError: (text: unknown) => setError(displayError(text)) } });
  return <><SidebarRail activeView={view} onSelectView={setView} changedCount={7}/><output data-testid="view">{view}</output>{error && <p role="alert">{errorText(error, locale)}</p>}</>;
}
createRoot(document.getElementById("root")!).render(<Fixture/>);
