// Offline provider: never POST recorded audio to a non-loopback host.
export function assertLoopbackUrl(baseURL: string): void {
  let url: URL;
  try {
    url = new URL(baseURL);
  } catch {
    throw new Error(`Invalid Whisper.cpp URL: ${baseURL}`);
  }
  const host = url.hostname.toLowerCase().replace(/^\[|\]$/g, "");
  const loopback =
    host === "localhost" || host === "::1" || /^127(\.\d{1,3}){3}$/.test(host);
  if (!loopback || !["http:", "https:"].includes(url.protocol) || url.username || url.password) {
    throw new Error(
      "Whisper.cpp must run on a loopback address (localhost or 127.x.x.x) to keep transcription local.",
    );
  }
}
