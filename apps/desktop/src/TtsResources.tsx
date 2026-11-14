import { t as translateMessage } from "./localization";
import { useEffect, useState } from "react";
import { invoke, isTauri } from "@tauri-apps/api/core";
import { t } from "./localization";

type Weights = {repo_id:string; revision:string; mode:string; managed?:boolean};
type TtsStatus = Weights & {piper?: Weights & {available:boolean}};

const resources = [
  ["Browse audio.cpp model packages", "https://huggingface.co/audio-cpp/audio.cpp-gguf"],
  ["Supertonic voices and model card", "https://huggingface.co/Supertone/supertonic-3"],
  ["audio.cpp compatibility guide", "https://github.com/0xShug0/audio.cpp/blob/main/docs/tts.md#supertonic"],
  ["Browse Piper voices", "https://huggingface.co/rhasspy/piper-voices"],
] as const;

export function TtsResources() {
  const [error, setError] = useState("");
  const [weights, setWeights] = useState<TtsStatus>();
  useEffect(() => {
    let active = true;
    if (isTauri()) void invoke<TtsStatus>("backend", {
      method:"services.read", params:{name:"tts.model.status", args:{}},
    }).then(value => { if(active) setWeights(value); }).catch(reason => { if(active) setError(String(reason)); });
    return () => {active=false;};
  }, []);
  return <section aria-label={t("TTS models and voices")} className="tts-resources">
    <h4>{t("TTS models and voices")}</h4>
    {weights && <p aria-label={t("Selected Supertonic weights")}>{t("Selected Supertonic weights")}: {weights.managed === false ? t("Built-in Supertonic 3") : weights.repo_id}{weights.revision ? ` @ ${weights.revision.slice(0,10)}` : ""} · {translateMessage(weights.mode === "cpu" ? "CPU" : "Vulkan")}</p>}
    {weights?.piper && <p aria-label={t("Selected Piper weights")}>{t("Selected Piper weights")}: {weights.piper.available ? <>{weights.piper.managed === false ? t("Built-in Piper Denis") : weights.piper.repo_id}{weights.piper.revision ? ` @ ${weights.piper.revision.slice(0,10)}` : ""} · {t("CPU")}</> : t("Unavailable")}</p>}
    <div className="row">
      {resources.map(([label, url]) => <a key={url} href={url} rel="noopener noreferrer" onClick={event => {
        event.preventDefault();
        setError("");
        if (isTauri()) void invoke("open_external_url", {url}).catch(reason => setError(String(reason)));
        else window.open(url, "_blank", "noopener,noreferrer");
      }}>{t(label)}</a>)}
      <button onClick={() => window.dispatchEvent(new CustomEvent("jarvis:model-library", {detail:"tts"}))}>{t("Manage TTS downloads")}</button>
    </div>
    <p className="note">{t("Activate compatible Supertonic GGUF or Piper ONNX/JSON weights in Model Library, then choose the synthesis engine and speaker here. Piper uses CPU. Check the model license before use.")}</p>
    {error && <p role="alert">{error}</p>}
  </section>;
}
