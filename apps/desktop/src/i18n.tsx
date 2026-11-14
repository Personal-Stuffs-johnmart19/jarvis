import "./i18n.css";
import { useId, useSyncExternalStore } from "react";
import { Select } from "radix-ui";
import { getLocale, isLocale, localeDefinitions, supportedLocales, localeKey, setLocale, t, translate } from "./localization";

function subscribe(onChange: () => void) {
  const storage = (event: StorageEvent) => {
    if (event.key === localeKey) onChange();
  };
  window.addEventListener("storage", storage);
  window.addEventListener("jarvis:language", onChange);
  return () => {
    window.removeEventListener("storage", storage);
    window.removeEventListener("jarvis:language", onChange);
  };
}
export function useLocale() {
  const locale = useSyncExternalStore(
    subscribe,
    getLocale,
    () => "en" as const,
  );
  document.documentElement.lang = locale;
  return locale;
}
export function LanguagePicker() {
  const locale = useLocale();
  const labelId = useId();
  return (
    <div className="language-picker">
      <span id={labelId}>{t("Interface language")}</span>
      <Select.Root
        value={locale}
        onValueChange={(value) => {
          if (isLocale(value)) setLocale(value);
        }}
      >
        <Select.Trigger className="language-picker-trigger" aria-labelledby={labelId}>
          <Select.Value />
          <Select.Icon className="language-picker-chevron" aria-hidden="true">
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
              <path d="m4 6 4 4 4-4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </Select.Icon>
        </Select.Trigger>
        <Select.Portal>
          <Select.Content
            className="language-picker-content"
            position="popper"
            side="top"
            align="start"
            sideOffset={6}
            collisionPadding={8}
          >
            <Select.ScrollUpButton className="language-picker-scroll" aria-hidden="true">▲</Select.ScrollUpButton>
            <Select.Viewport className="language-picker-viewport">
              {supportedLocales.map(id => (
                <Select.Item
                  className="language-picker-option"
                  key={id}
                  value={id}
                  textValue={translate(id, localeDefinitions[id].label)}
                >
                  <Select.ItemText><span lang={id}>{translate(id, localeDefinitions[id].label)}</span></Select.ItemText>
                  <Select.ItemIndicator className="language-picker-check" aria-hidden="true">✓</Select.ItemIndicator>
                </Select.Item>
              ))}
            </Select.Viewport>
            <Select.ScrollDownButton className="language-picker-scroll" aria-hidden="true">▼</Select.ScrollDownButton>
          </Select.Content>
        </Select.Portal>
      </Select.Root>
    </div>
  );
}
