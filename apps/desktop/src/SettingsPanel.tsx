import { confirmAction } from "./confirmAction";
import { errorText, type DisplayError } from "./displayError";
import { applySettingsAction, settingsMessage, type SettingsRun } from "./settingsFeedback";
import { speechPreview as previewSample } from "./speechPreview";
import { t as translateMessage } from "./localization";
import { microphoneChoices } from "./microphoneChoices";
import { BrowserSettings } from "./BrowserSettings";
import { TtsResources } from "./TtsResources";
import { synthesisChoices, synthesisId } from "./speechChoices";
import { t } from "./localization";
import { useEffect, useRef, useState } from "react";

import {
  ArrowDown,
  ArrowUp,
  Pause,
  Play,
  Plus,
  Square,
  Trash2,
  GripVertical,
} from "lucide-react";
import type { Snapshot } from "./bridge";

type Hint = [string, string];
type PanelProps = {
  tab: string;
  snapshot: Snapshot | null;
  hints: Hint[];
  setHints: (value: Hint[]) => void;
  run: SettingsRun;
  refresh: () => Promise<void>;
  setTab: (value: string) => void;
};
export function SettingsPanel({
  tab,
  snapshot,
  hints,
  setHints,
  run,
  refresh,
  setTab,
}: PanelProps) {
  const [microphones, setMicrophones] = useState<
      { id: number; name: string }[]
    >([]),
    [microphone, setMicrophone] = useState(String(snapshot?.microphone ?? ""));
  const [microphonesLoaded, setMicrophonesLoaded] = useState(false);
  useEffect(() => {
    if (tab !== "Speech & Voice") return;
    let active = true;
    setMicrophonesLoaded(false);
    run<{ id: number; name: string }[]>("audio.devices").then(value => {
      if (active && value) {
        setMicrophones(value);
        setMicrophonesLoaded(true);
      }
    });
    return () => { active = false; };
  }, [tab]);
  const [status, setStatus] = useState<DisplayError>(""),
    [voice, setVoice] = useState(snapshot?.voice || ""),
    [voices, setVoices] = useState(snapshot?.voices || []),
    [mode, setMode] = useState(snapshot?.network.mode || "auto"),
    [proxy, setProxy] = useState(snapshot?.network.proxy || ""),
    [items, setItems] = useState<Hint[]>(hints),
    [selected, setSelected] = useState<number | null>(null),
    [name, setName] = useState(""),
    [prompt, setPrompt] = useState(""),
    [urls, setUrls] = useState(""),
    [playlist, setPlaylist] = useState(""),
    [queue, setQueue] = useState<any>(null),
    [devices, setDevices] = useState<any[]>([]),
    [camera, setCamera] = useState(""),
    [allowed, setAllowed] = useState(false),
    [frame, setFrame] = useState("");
  const [synthesisControls, setSynthesisControls] = useState<{language:string; automatic_speed:boolean; speed:number; inference_steps:number}>();
  const [synthesisLanguages, setSynthesisLanguages] = useState<string[]>([]);
  const [speechPreview, setSpeechPreview] = useState<string>();
  const previewText = previewSample(synthesisControls?.language, speechPreview);
  useEffect(() => {
    if (tab !== "Speech & Voice") return;
    let active=true;
    run<{values:NonNullable<typeof synthesisControls>; languages:string[]; warning?:string}>("services.read",{name:"tts.controls",args:{}}).then(value=>{
      if(active && value){setSynthesisControls(value.values);setSynthesisLanguages(value.languages);if(value.warning)setStatus(settingsMessage("Saved speech controls are invalid. Save these defaults to reset them."));}
    });
    return ()=>{active=false;};
  },[tab]);
  const [applying, setApplying] = useState(false);
  const applyingRef = useRef(false);
  const drag = useRef<number | null>(null);
  const apply = async (method: string, params: unknown = {}) => {
    if (applyingRef.current) return undefined;
    applyingRef.current = true;
    setApplying(true);
    try {
      return await applySettingsAction(run, method, params, setStatus);
    } finally {
      applyingRef.current = false;
      setApplying(false);
    }
  };
  useEffect(() => {
    setStatus("");
    if (tab === "Queue")
      run<any>("browser.action", { action: "queue_list" }).then(
        (data) => data && setQueue(data),
      );
    if (tab === "Camera")
      run<any[]>("camera.list").then((data) => {
        if (data) {
          setDevices(data);
          setCamera(String(data[0]?.index ?? ""));
        }
      });
  }, [tab]);
  useEffect(() => {
    const handler = (event: Event) => setFrame((event as CustomEvent).detail);
    window.addEventListener("camera-frame", handler);
    return () => {
      window.removeEventListener("camera-frame", handler);
      if (tab === "Camera")
        run("camera.command", { action: "status", preview: false });
    };
  }, [tab]);
  useEffect(() => {
    if (tab !== "Speech & Voice") return;
    let active = true;
    run<any[]>("voices").then(data => { if (active && data) setVoices(data); });
    return () => { active = false; };
  }, [tab, snapshot?.key_configured]);
  const saveHints = async (next: Hint[]) => {
    const saved = await apply("suggestions.save", { items: next });
    if (saved) {
      setItems(saved);
      setHints(saved);
    }
    return !!saved;
  };
  const reorder = async (from: number, to: number) => {
    if (from === to) return;
    const next = [...items];
    next.splice(to, 0, next.splice(from, 1)[0]);
    if (await saveHints(next)) setSelected(to);
  };
  const queueAction = async (action: string, extra = {}) => {
    const value = await apply("browser.action", { action, ...extra });
    if (value) setQueue(value);
  };
  const microphoneOptions = microphoneChoices(microphones, microphonesLoaded ? microphone : "", t);
  const microphoneUnavailable = microphonesLoaded && microphoneOptions.some(item => item.value === microphone && item.unavailable);
  const synthesisModels = synthesisChoices(voices);
  const synthesisModel = synthesisModels.find(model => model.id === synthesisId(voice));
  return (
    <>
      <fieldset
        className="settings-content settings-fields"
        key={tab}
        disabled={applying}
        aria-busy={applying}
      >
        {tab === "Network & Proxy" && (
          <>
            <p>
              {" "}
              {t(
                "Gemini-only route. On startup - standard connection, then fallback.",
              )}
            </p>
            <label>
              {t("Mode")}
              <select value={mode} onChange={(e) => setMode(e.target.value)}>
                <option value="auto"> {t("Automatic Route")}</option>
                <option value="system">
                  {" "}
                  {t("Standard / System Connection")}
                </option>
                <option value="proxy">{t("Custom HTTP/HTTPS Proxy")}</option>
              </select>
            </label>
            <label>
              {" "}
              {t("Proxy")}
              <input
                value={proxy}
                onChange={(e) => setProxy(e.target.value)}
                placeholder={translateMessage("http://host:port")}
                disabled={mode === "system"}
              />
            </label>
            <div className="row">
              <button
                className="primary"
                onClick={async () => {
                  await apply("settings.network", { mode, proxy });
                  await refresh();
                }}
              >
                {" "}
                {t("Save")}
              </button>
              <button onClick={() => apply("network.check")}>
                {t("Test Connection")}
              </button>
              <button onClick={() => apply("network.check", { model: true })}>
                {" "}
                {t("Test Gemini Response")}
              </button>
            </div>
          </>
        )}
        {tab === "Speech & Voice" && (
          <>
            <label>
              {" "}
              {t("Microphone")}
              <select
                value={microphone}
                onChange={(event) => setMicrophone(event.target.value)}
              >
                <option value=""> {t("System Default")}</option>
                {microphoneOptions.map((item) => (
                  <option key={item.value} value={item.value} disabled={item.unavailable}>
                    {item.label}
                  </option>
                ))}
              </select>
            </label>
            <p className="note">{t("Device names come from Windows and are kept unchanged. Device numbers distinguish duplicate names.")}</p>
            {microphoneUnavailable && <p role="status">{t("Reconnect the selected microphone or choose another device. Your selection has not been changed.")}</p>}
            <button
              disabled={microphoneUnavailable}
              onClick={() =>
                apply("settings.microphone", {
                  microphone: microphone === "" ? null : Number(microphone),
                })
              }
            >
              {" "}
              {t("Select Microphone")}
            </button>
            <p>
              {t(
                "Supertonic runs locally via Vulkan with CPU fallback. Gemini TTS is a separate cloud mode using the same key as LLM; auto selects Lite up to 600 characters, Flash for longer responses.",
              )}
            </p>
            <TtsResources />
            <label>
              {t("TTS synthesis model")}
              <select
                aria-label={t("TTS synthesis model")}
                value={synthesisModel?.id ?? ""}
                onChange={(event) => {
                  const model = synthesisModels.find(
                    (item) => item.id === event.target.value,
                  );
                  if (model?.voices[0]) setVoice(model.voices[0].id);
                }}
              >
                {!synthesisModel && (
                  <option value="" disabled>
                    {t("Select a synthesis model")}
                  </option>
                )}
                {synthesisModels.map((model) => (
                  <option key={model.id} value={model.id}>
                    {model.id === "gemini:auto" ? t("Gemini TTS · Automatic") : model.name}
                  </option>
                ))}
              </select>
            </label>
            {voice && !synthesisModel && <p role="status">{t("The selected synthesis model is unavailable. Choose another model or configure its integration. Your saved voice has not been changed.")}</p>}
            {!synthesisModels.some(model => model.cloud) && <div className="note">
              <p>{t("Gemini speech choices require a configured shared Google API key. Local engines may obtain required assets on first use.")}</p>
              <button onClick={() => setTab("Integrations")}>{t("Configure integrations")}</button>
            </div>}
            <label>
              {t("Speaker voice")}
              {synthesisModel?.cloud ? (
                <input aria-label={t("Speaker voice")} value="Kore" readOnly />
              ) : (
                <select
                  aria-label={t("Speaker voice")}
                  value={voice}
                  disabled={!synthesisModel}
                  onChange={(event) => setVoice(event.target.value)}
                >
                  {synthesisModel?.voices.map((value) => (
                    <option key={value.id} value={value.id}>
                      {t(value.name)}
                    </option>
                  ))}
                </select>
              )}
            </label>
            {synthesisModel?.id === "piper" && <label>{t("Voice preview text")}
              <input aria-label={t("Voice preview text")} maxLength={512} value={speechPreview ?? ""} onChange={event=>setSpeechPreview(event.target.value)} disabled={applying}/>
            </label>}
            {synthesisModel?.id === "supertonic" && synthesisControls && <fieldset>
              <legend>{t("Supertonic synthesis controls")}</legend>
              <label>{t("Speech language")}
                <select aria-label={t("Speech language")} value={synthesisControls.language} onChange={event=>{setSynthesisControls({...synthesisControls,language:event.target.value});setSpeechPreview(undefined);}}>
                  {synthesisLanguages.map(language=><option key={language} value={language}>{language}</option>)}
                </select>
              </label>
              <label><input type="checkbox" checked={synthesisControls.automatic_speed} onChange={event=>setSynthesisControls({...synthesisControls,automatic_speed:event.target.checked})}/>{t("Automatic speech speed")}</label>
              <label>{t("Speech speed multiplier")}
                <input aria-label={t("Speech speed multiplier")} type="number" min="0.5" max="2" step="0.05" disabled={synthesisControls.automatic_speed} value={synthesisControls.speed}
                  onChange={event=>setSynthesisControls({...synthesisControls,speed:Number(event.target.value)})}/>
              </label>
              <label>{t("Inference steps")}
                <input aria-label={t("Inference steps")} type="number" min="1" max="32" step="1" value={synthesisControls.inference_steps}
                  onChange={event=>setSynthesisControls({...synthesisControls,inference_steps:Number(event.target.value)})}/>
              </label>
              <label>{t("Voice preview text")}
                <input aria-label={t("Voice preview text")} maxLength={512} value={previewText} onChange={event=>setSpeechPreview(event.target.value)}
                  placeholder={t("Enter text in the selected speech language")}/>
              </label>
              <p className="note">{t("More inference steps require more processing. Pause, intonation, style and separate quality packs are not supported by this adapter.")}</p>
            </fieldset>}
            {synthesisModel?.id === "qwen3" && <p className="note">{t("A fixed synthetic reference anchors this voice. Speech generation uses Vulkan; reference encoding runs once on CPU per engine session and is cached.")}</p>}
            <p className="note">
              {synthesisModel?.cloud
                ? t(
                    "The current Gemini adapter uses the Kore speaker. Model selection controls the cloud synthesis model and its existing fallback chain.",
                  )
                : t(
                    "Speaker presets belong to the selected synthesis model. Runtime and required assets are installed on first use.",
                  )}
            </p>
            <p className="note">
              {t(
                "Separate voice-quality packs, pause, intonation and style controls are not exposed by the current adapters. Sentence pauses and punctuation handling remain automatic.",
              )}
            </p>
            <div className="row">
              <button
                className="primary"
                disabled={applying || !synthesisModel || (synthesisModel.id === "supertonic" && !synthesisControls)}
                onClick={async () => {
                  await apply("settings.voice", { voice, ...(synthesisModel?.id === "supertonic" && synthesisControls ? {synthesis:synthesisControls} : {}) });
                  await refresh();
                }}
              >
                {t("Save")}
              </button>
              <button
                disabled={applying || !synthesisModel || (synthesisModel.id === "supertonic" && (!synthesisControls || !previewText.trim()))}
                onClick={() =>
                  apply("settings.voice", { voice, preview: true, ...(synthesisModel?.id === "supertonic" ? {preview_text:previewText} : synthesisModel?.id === "piper" ? {preview_text:speechPreview ?? ""} : {}), ...(synthesisModel?.id === "supertonic" && synthesisControls ? {synthesis:synthesisControls} : {}) })
                }
              >
                <Play /> {t("Listen")}
              </button>
            </div>
            <p className="note">
              {t(
                "When Gemini TTS is selected, the response text is sent to Google. Free Tier depends on project and quotas; unavailable models are replaced by fallback. Local voices do not send text to the cloud. Silero is for non-commercial use.",
              )}
            </p>
          </>
        )}
        {tab === "Browser" && <BrowserSettings preferences={snapshot?.browser} run={run} apply={apply} refresh={refresh} />}
        {tab === "Prompts" && (
          <>
            <p>
              {t(
                "Drag and drop or use the arrows. New order is saved immediately.",
              )}
            </p>
            <ol className="hint-list">
              {items.map(([label, value], index) => (
                <li
                  key={index}
                  draggable
                  onDragStart={() => {
                    drag.current = index;
                  }}
                  onDragOver={(e) => e.preventDefault()}
                  onDrop={(e) => {
                    e.preventDefault();
                    if (drag.current !== null) reorder(drag.current, index);
                    drag.current = null;
                  }}
                >
                  <button
                    className={selected === index ? "selected" : ""}
                    onClick={() => {
                      setSelected(index);
                      setName(label);
                      setPrompt(value);
                    }}
                  >
                    <GripVertical />
                    {label}
                  </button>
                  <button
                    aria-label={t("Move Up {0}", label)}
                    disabled={index === 0}
                    onClick={() => reorder(index, index - 1)}
                  >
                    <ArrowUp />
                  </button>
                  <button
                    aria-label={t("Move Down {0}", label)}
                    disabled={index === items.length - 1}
                    onClick={() => reorder(index, index + 1)}
                  >
                    <ArrowDown />
                  </button>
                  <button
                    aria-label={t("Delete {0}", label)}
                    onClick={async () => {
                      if (await confirmAction(t('Delete "{0}"?', label)))
                        saveHints(items.filter((_, n) => n !== index)).then(
                          () => setSelected(null),
                        );
                    }}
                  >
                    <Trash2 />
                  </button>
                </li>
              ))}
            </ol>
            <button
              onClick={() => {
                setSelected(null);
                setName("");
                setPrompt("");
              }}
            >
              <Plus /> {t("Add")}
            </button>
            <label>
              {t("Name")}
              <input
                maxLength={60}
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </label>
            <label>
              {t("Command text")}
              <textarea
                rows={4}
                maxLength={8000}
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
              />
            </label>
            <button
              className="primary"
              onClick={() => {
                const next = [...items];
                if (selected === null) next.push([name, prompt]);
                else next[selected] = [name, prompt];
                saveHints(next).then((ok) => {
                  if (ok) setSelected(selected ?? next.length - 1);
                });
              }}
            >
              {t("Save Prompt")}
            </button>
          </>
        )}
        {tab === "Queue" && (
          <>
            <p>
              {" "}
              {t(
                "Videos and playlists are saved locally in Jarvis, not in your YouTube account.",
              )}
            </p>
            <label>
              {" "}
              {t("YouTube links, one per line")}
              <textarea
                rows={3}
                value={urls}
                onChange={(e) => setUrls(e.target.value)}
              />
            </label>
            <div className="row">
              <button
                disabled={!urls.trim()}
                onClick={() =>
                  queueAction("queue_add", {
                    urls: urls
                      .split("\n")
                      .map((v) => v.trim())
                      .filter(Boolean),
                  })
                }
              >
                <Plus />
                {t("Add")}
              </button>
              <button onClick={() => queueAction("queue_play")}>
                <Play /> {t("Play")}
              </button>
              <button onClick={() => queueAction("queue_next")}>
                {" "}
                {t("Next")}
              </button>
              <button onClick={() => queueAction("queue_pause")}>
                <Pause /> {t("Pause")}
              </button>
              <button
                onClick={async () => {
                  if (await confirmAction(t("Clear queue?"))) queueAction("queue_clear");
                }}
              >
                <Trash2 /> {t("Clear")}
              </button>
            </div>
            <ol className="queue-list">
              {queue?.queue?.map((url: string, index: number) => (
                <li key={index}>
                  <span>
                    {translateMessage(queue.position === index ? "▶ " : "")}
                    {url}
                  </span>
                  <button
                    aria-label={t("Remove video")}
                    onClick={() => queueAction("queue_remove", { index })}
                  >
                    <Trash2 />
                  </button>
                </li>
              ))}
            </ol>
            <label>
              {t("Playlist name")}
              <input
                value={playlist}
                onChange={(e) => setPlaylist(e.target.value)}
                list="playlists"
              />
              <datalist id="playlists">
                {queue?.playlists?.map((name: string) => (
                  <option key={name}>{name}</option>
                ))}
              </datalist>
            </label>
            <div className="row">
              <button
                onClick={() => queueAction("playlist_save", { playlist })}
              >
                {" "}
                {t("Save Playlist")}
              </button>
              <button
                onClick={() => queueAction("playlist_load", { playlist })}
              >
                {" "}
                {t("Load")}
              </button>
              <button
                onClick={() => queueAction("playlist_append", { playlist })}
              >
                {" "}
                {t("Add to Queue")}
              </button>
              <button
                onClick={async () => {
                  if (await confirmAction(t("Delete playlist?")))
                    queueAction("playlist_delete", { playlist });
                }}
              >
                {" "}
                {t("Delete Playlist")}
              </button>
            </div>
          </>
        )}
        {tab === "Camera" && (
          <>
            <p>
              {t(
                "Camera is optional and disabled on every startup. Selecting a device does not start recording.",
              )}
            </p>
            <label>
              {t("Device")}
              <select
                disabled={!devices.length}
                value={camera}
                onChange={(e) => {
                  setCamera(e.target.value);
                  setAllowed(false);
                  run("camera.allow", { enabled: false });
                }}
              >
                {!devices.length && <option>{t("No cameras found")}</option>}
                {devices.map((device) => (
                  <option key={device.index} value={device.index}>
                    {device.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={allowed}
                disabled={!devices.length}
                onChange={async (e) => {
                  const next = e.target.checked;
                  const value = await apply("camera.allow", {
                    enabled: next,
                    index: Number(camera),
                  });
                  if (value) setAllowed(next);
                  if (!next) setFrame("");
                }}
              />
              {t("Allow camera for this session")}
            </label>
            {frame && allowed && (
              <img
                className="camera-preview"
                src={"data:image/jpeg;base64," + frame}
                alt={t("Selected camera preview")}
              />
            )}
            <div className="tool-grid">
              {[
                ["gesture", t("Gestures — recognize hands")],
                ["objects", t("Objects — search for items")],
                ["air_mouse", t("Gesture Mouse")],
                ["volume", t("Gesture Volume")],
                ["posture", t("Body Pose")],
                ["motion", t("Motion")],
              ].map(([mode, label]) => (
                <button
                  disabled={!allowed}
                  key={mode}
                  onClick={() =>
                    apply("camera.command", { action: "start", mode })
                  }
                >
                  {label}
                </button>
              ))}
              <button
                disabled={!allowed}
                onClick={() =>
                  apply("camera.command", {
                    action: "start",
                    mode: "gesture",
                    preview: true,
                  })
                }
              >
                {" "}
                {t("Open Preview")}
              </button>
              {[
                ["qr", t("Read QR Code")],
                ["ocr", t("Read Text")],
                ["describe", t("Describe Scene")],
                ["face_count", t("Count Faces")],
                ["record_start", t("Start Recording")],
                ["record_stop", t("Stop Recording")],
                ["background_remove", t("Remove Background")],
              ].map(([action, label]) => (
                <button
                  disabled={!allowed}
                  key={action}
                  onClick={() => apply("camera.command", { action })}
                >
                  {label}
                </button>
              ))}
            </div>
            <button
              onClick={async () => {
                await apply("camera.allow", { enabled: false });
                setAllowed(false);
                setFrame("");
              }}
            >
              {" "}
              {t("Turn Off Camera")}
            </button>
          </>
        )}
      </fieldset>
      {tab === "Speech & Voice" && (
        <div className="settings-content row">
          <button onClick={() => run("control", { action: "speech_stop" })}>
            <Square /> {t("Stop")}
          </button>
        </div>
      )}
      {status && (
        <p className="settings-status" role="status">
          {errorText(status)}
        </p>
      )}
    </>
  );
}
