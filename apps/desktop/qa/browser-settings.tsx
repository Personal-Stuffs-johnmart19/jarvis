import { useState } from "react";
import { createRoot } from "react-dom/client";
import { BrowserSettings } from "../src/BrowserSettings";
import type { BrowserPreferences } from "../src/browserSelection";

const qa = (window as any).__browserSettingsQA = { calls: [] as any[], finishProfiles: null as any, setPreferences: null as any };
const run = async (method: string) => {
  if (method !== "browser.profiles") throw new Error("Unexpected real service call");
  return await new Promise<any>(resolve => { qa.finishProfiles = resolve; });
};
function Fixture() {
  const [preferences, setPreferences] = useState<BrowserPreferences>();
  qa.setPreferences = setPreferences;
  return <BrowserSettings preferences={preferences} run={run} apply={async (method, params) => { qa.calls.push({method, params}); return {ok:true}; }} refresh={async () => {}} />;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
