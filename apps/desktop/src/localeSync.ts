import type { Locale } from "./localization";

/** Keep backend locale writes ordered and allow recovery after a failed request. */
export function createLocaleSync(send: (locale: Locale) => Promise<unknown>) {
  let tail = Promise.resolve();
  return (locale: Locale): Promise<void> => {
    const request = tail.then(() => send(locale)).then(() => undefined);
    tail = request.catch(() => undefined);
    return request;
  };
}
