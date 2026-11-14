import { displayError, errorText, type DisplayError } from "../../../../src/displayError";
import { t as translateMessage } from "../../../../src/localization";
import { t } from "../../../../src/localization";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectSeparator,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";

import { usePreferencesStore } from "@/modules/settings/preferences";
import {
  EDITOR_THEME_AUTO,
  EDITOR_THEME_LABELS,
  EDITOR_THEME_MODE,
  EDITOR_THEMES,
  type EditorThemePref,
  setBackgroundBlur,
  setBackgroundImageId,
  setBackgroundKind,
  setBackgroundOpacity,
  setEditorTheme,
  setWindowVibrancy,
} from "@/modules/settings/store";
import { useTheme } from "@/modules/theme";
import {
  deleteBgImage,
  importBgImageFromFile,
} from "@/modules/theme/bgImageStore";
import {
  deleteCustomTheme,
  saveCustomTheme,
} from "@/modules/theme/customThemes";
import {
  deleteThemeFile,
  handOffThemeEdit,
  type ThemeEditRequest,
} from "@/modules/theme/themeFiles";
import { listBuiltinThemes } from "@/modules/theme/themes";
import { DEFAULT_THEME_ID } from "@/modules/theme/types";
import { validateTheme } from "@/modules/theme/validateTheme";
import { type Backdrop, getBackdropKind } from "@/modules/theme/vibrancy";
import { PlusSignIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { getCurrentWindow } from "@/host/window";
import { useEffect, useMemo, useRef, useState } from "react";
import { SectionHeader } from "../components/SectionHeader";
import { SettingRow } from "../components/SettingRow";
import { ThemeCard } from "../components/ThemeCard";

export function ThemesSection() {
  const { themeId, setThemeId, resolvedMode, customThemes } = useTheme();
  const builtinThemes = listBuiltinThemes();
  const themes = useMemo(
    () => [...builtinThemes, ...customThemes],
    [builtinThemes, customThemes],
  );
  const customIds = useMemo(
    () => new Set(customThemes.map((t) => t.id)),
    [customThemes],
  );

  const [importError, setImportError] = useState<{ name: string; error: DisplayError } | null>(null);
  const [deleteError, setDeleteError] = useState<DisplayError>("");
  const [bgError, setBgError] = useState<DisplayError>("");
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const bgInputRef = useRef<HTMLInputElement | null>(null);

  // Editing happens in the main window's code editor, so Settings closes
  // (destroying its WebContent process) rather than hiding and staying resident.
  const startThemeEdit = (req: ThemeEditRequest) =>
    void handOffThemeEdit(req, () => getCurrentWindow().close());
  const onCreateTheme = () => startThemeEdit({ action: "create" });
  const onEditTheme = (id: string) => startThemeEdit({ action: "edit", id });

  const editorThemePref = usePreferencesStore((s) => s.editorTheme);
  const backgroundKind = usePreferencesStore((s) => s.backgroundKind);
  const backgroundImageId = usePreferencesStore((s) => s.backgroundImageId);
  const backgroundOpacity = usePreferencesStore((s) => s.backgroundOpacity);
  const backgroundBlur = usePreferencesStore((s) => s.backgroundBlur);
  const windowVibrancy = usePreferencesStore((s) => s.windowVibrancy);

  const [backdrop, setBackdrop] = useState<Backdrop>("none");
  useEffect(() => {
    let alive = true;
    void getBackdropKind().then((k) => {
      if (alive) setBackdrop(k);
    });
    return () => {
      alive = false;
    };
  }, []);

  const handleThemeFiles = async (files: FileList | null) => {
    setImportError(null);
    if (!files || files.length === 0) return;
    for (const file of Array.from(files)) {
      try {
        const text = await file.text();
        const parsed = JSON.parse(text);
        const result = validateTheme(parsed);
        if (!result.ok) {
          setImportError({ name: file.name, error: result.issue ?? result.error });
          return;
        }
        await saveCustomTheme(result.theme);
        setThemeId(result.theme.id);
      } catch (e) {
        setImportError({ name: file.name, error: displayError(e) });
        return;
      }
    }
  };

  const onPickThemeFile = () => fileInputRef.current?.click();

  const onRemoveCustomTheme = async (id: string) => {
    setDeleteError("");
    try {
      await deleteThemeFile(id);
      await deleteCustomTheme(id);
      if (themeId === id) setThemeId(DEFAULT_THEME_ID);
    } catch (error) {
      setDeleteError(displayError(error));
    }
  };

  const onPickBgFile = () => bgInputRef.current?.click();

  const handleBgFiles = async (files: FileList | null) => {
    setBgError("");
    if (!files || files.length === 0) return;
    const file = files[0];
    if (!file.type.startsWith("image/")) {
      setBgError({ source: "jarvis", message: "{0}: not an image", values: [file.name] });
      return;
    }
    try {
      const prev = backgroundImageId;
      const { id } = await importBgImageFromFile(file);
      await setBackgroundImageId(id);
      await setBackgroundKind("image");
      if (prev && prev !== id) await deleteBgImage(prev).catch(() => undefined);
    } catch (e) {
      setBgError(displayError(e));
    }
  };

  const onRemoveBackground = async () => {
    setBgError("");
    try {
      const prev = backgroundImageId;
      await setBackgroundKind("none");
      await setBackgroundImageId(null);
      if (prev) await deleteBgImage(prev).catch(() => undefined);
    } catch (error) {
      setBgError(displayError(error));
    }
  };

  return (
    <div className="flex flex-col gap-6">
      <SectionHeader
        title={t("Themes")}
        description={t("Theme, background image, and customization.")}
      />

      {backdrop === "none" ? null : (
        <SettingRow
          title={translateMessage(backdrop === "mica" ? "Mica background" : "Window vibrancy")}
          description={
            translateMessage(backdrop === "mica"
              ? "Blend the header, status bar and gutters into the desktop wallpaper. Windows 11 only."
              : "Frost the header, status bar and gutters over what is behind the window. Panes stay solid.")
          }
        >
          <Switch
            checked={windowVibrancy}
            onCheckedChange={(v) => void setWindowVibrancy(v)}
          />
        </SettingRow>
      )}

      <div
        role="presentation"
        className="flex flex-col gap-2"
        onDragOver={(e) => {
          e.preventDefault();
          e.dataTransfer.dropEffect = "copy";
        }}
        onDrop={(e) => {
          e.preventDefault();
          void handleThemeFiles(e.dataTransfer.files);
        }}
      >
        <div className="flex items-center justify-between">
          <Label>{t("Theme")}</Label>
          <div className="flex items-center gap-1.5">
            <Button
              variant="outline"
              size="sm"
              className="h-7 gap-1.5 px-2 text-[11px]"
              onClick={onCreateTheme}
            >
              <HugeiconsIcon icon={PlusSignIcon} size={11} strokeWidth={2} />
              {t("Create")}{" "}
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="h-7 px-2 text-[11px]"
              onClick={onPickThemeFile}
            >
              {t("Import .jarvis-theme")}{" "}
            </Button>
          </div>
          <input
            ref={fileInputRef}
            type="file"
            accept=".jarvis-theme,.terax-theme,.json,application/json"
            className="hidden"
            onChange={(e) => {
              void handleThemeFiles(e.target.files);
              e.target.value = "";
            }}
          />
        </div>
        {deleteError ? (
          <div role="alert" className="rounded-md border border-destructive/40 bg-destructive/10 px-2.5 py-1.5 text-[11.5px] text-destructive">
            {t("Could not delete theme: {0}", errorText(deleteError))}
          </div>
        ) : null}
        {importError ? (
          <div role="alert" className="rounded-md border border-destructive/40 bg-destructive/10 px-2.5 py-1.5 text-[11.5px] text-destructive">
            {importError.name}: {errorText(importError.error)}
          </div>
        ) : null}
        <div className="grid grid-cols-2 gap-2">
          {themes.map((theme) => (
            <ThemeCard key={theme.id} theme={theme} mode={resolvedMode}
              selected={themeId === theme.id} custom={customIds.has(theme.id)}
              onSelect={() => setThemeId(theme.id)}
              onEdit={() => onEditTheme(theme.id)}
              onRemove={() => void onRemoveCustomTheme(theme.id)} />
          ))}
        </div>
      </div>

      <div className="flex flex-col gap-2">
        <div className="flex items-center justify-between gap-3">
          <div className="flex min-w-0 flex-col">
            <Label>{t("Editor theme")}</Label>
            <span className="text-[11px] text-muted-foreground">
              {t(
                "Syntax colors for the code editor. Auto follows the app theme.",
              )}{" "}
            </span>
          </div>
          <Select
            value={editorThemePref}
            onValueChange={(v) => void setEditorTheme(v as EditorThemePref)}
          >
            <SelectTrigger size="sm" className="h-8 w-44 text-[12px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={EDITOR_THEME_AUTO} className="text-[12px]">
                {t("Auto (match app theme)")}{" "}
              </SelectItem>
              <SelectSeparator />
              {[...EDITOR_THEMES]
                .sort(
                  (a, b) =>
                    (EDITOR_THEME_MODE[a] === resolvedMode ? 0 : 1) -
                    (EDITOR_THEME_MODE[b] === resolvedMode ? 0 : 1),
                )
                .map((id) => (
                  <SelectItem
                    key={id}
                    value={id}
                    disabled={EDITOR_THEME_MODE[id] !== resolvedMode}
                    className="text-[12px]"
                  >
                    {EDITOR_THEME_LABELS[id]}
                  </SelectItem>
                ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      <div
        role="presentation"
        className="flex flex-col gap-2"
        onDragOver={(e) => {
          e.preventDefault();
          e.dataTransfer.dropEffect = "copy";
        }}
        onDrop={(e) => {
          e.preventDefault();
          void handleBgFiles(e.dataTransfer.files);
        }}
      >
        <div className="flex items-center justify-between">
          <Label>{t("Background")}</Label>
          <div className="flex items-center gap-2">
            {backgroundKind === "image" && backgroundImageId ? (
              <Button
                variant="ghost"
                size="sm"
                className="h-7 px-2 text-[11px] text-muted-foreground hover:text-destructive"
                onClick={() => void onRemoveBackground()}
              >
                {t("Remove")}{" "}
              </Button>
            ) : null}
            <Button
              variant="outline"
              size="sm"
              className="h-7 px-2 text-[11px]"
              onClick={onPickBgFile}
            >
              {translateMessage(backgroundKind === "image" ? "Replace image" : "Choose image")}
            </Button>
            <input
              ref={bgInputRef}
              type="file"
              accept="image/*"
              className="hidden"
              onChange={(e) => {
                void handleBgFiles(e.target.files);
                e.target.value = "";
              }}
            />
          </div>
        </div>
        {bgError ? (
          <div role="alert" className="rounded-md border border-destructive/40 bg-destructive/10 px-2.5 py-1.5 text-[11.5px] text-destructive">
            {errorText(bgError)}
          </div>
        ) : null}
        {backgroundKind === "image" && backgroundImageId ? (
          <div className="flex flex-col gap-3 rounded-lg border border-border/60 p-3">
            <div className="flex items-center justify-between gap-3">
              <span className="text-[11.5px] text-muted-foreground">
                {t("Opacity")}{" "}
              </span>
              <span className="tabular-nums text-[11px] text-muted-foreground">
                {Math.round(backgroundOpacity * 100)}%
              </span>
            </div>
            <Slider
              value={[backgroundOpacity]}
              min={0}
              max={1}
              step={0.01}
              onValueChange={(v) => void setBackgroundOpacity(v[0] ?? 0)}
            />
            <div className="flex items-center justify-between gap-3 pt-1">
              <span className="text-[11.5px] text-muted-foreground">
                {t("Blur")}
              </span>
              <span className="tabular-nums text-[11px] text-muted-foreground">
                {backgroundBlur}
                {t("px")}{" "}
              </span>
            </div>
            <Slider
              value={[backgroundBlur]}
              min={0}
              max={64}
              step={1}
              onValueChange={(v) => void setBackgroundBlur(v[0] ?? 0)}
            />
          </div>
        ) : (
          <p className="text-[11px] text-muted-foreground">
            {t(
              "Drop an image here or pick one. Stored locally; doesn't affect the default look until set.",
            )}{" "}
          </p>
        )}
      </div>
    </div>
  );
}

function Label({ children }: { children: React.ReactNode }) {
  return (
    <span className="text-[11px] font-medium tracking-tight text-muted-foreground">
      {children}
    </span>
  );
}
