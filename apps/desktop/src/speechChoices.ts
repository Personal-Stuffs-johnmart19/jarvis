export type VoiceChoice = { id: string; name: string };
export type SynthesisChoice = {
  id: string;
  name: string;
  voices: VoiceChoice[];
  cloud: boolean;
};
const families: Record<string, string> = {
  supertonic: "Supertonic 3 · audio.cpp",
  qwen3: "Qwen3 · audio.cpp",
  silero: "Silero",
  piper: "Piper",
  sapi: "Windows SAPI",
};
const cloudModels: Record<string, string> = {
  auto: "Gemini TTS · Automatic",
  flash: "Gemini 3.8 Flash TTS",
  lite: "Gemini 3.8 Flash-Lite TTS",
  preview: "Gemini 3.1 Flash TTS Preview",
};
export function synthesisId(voice: string): string {
  const family = voice.split(":", 1)[0];
  return family === "gemini" ? voice : family;
}
/** Build choices only from backend-advertised voices; selecting does not synthesize. */
export function synthesisChoices(voices: VoiceChoice[]): SynthesisChoice[] {
  const groups = new Map<string, SynthesisChoice>();
  for (const voice of voices) {
    const id = synthesisId(voice.id);
    const family = voice.id.split(":", 1)[0];
    if (!groups.has(id))
      groups.set(id, {
        id,
        name:
          family === "gemini"
            ? (cloudModels[voice.id.slice(7)] ?? voice.name)
            : (families[family] ?? family),
        voices: [],
        cloud: family === "gemini",
      });
    groups.get(id)!.voices.push(voice);
  }
  return [...groups.values()];
}
