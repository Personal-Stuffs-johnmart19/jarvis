import { t } from "../../../../src/localization";
import { getReducedMotionPreference, setReducedMotionPreference, subscribeReducedMotionPreference } from "../../../../src/motionPreference";
import { LanguagePicker } from "../../../../src/i18n";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { IS_MAC } from "@/lib/platform";
import { cn } from "@/lib/utils";
import {
  type OsNotificationResult,
  testAgentOsNotification,
} from "@/modules/agents/lib/notify";
import { usePreferencesStore } from "@/modules/settings/preferences";
import type { ThemePref } from "@/modules/settings/store";
import {
  setAgentNotificationSound,
  setAgentNotifications,
  setAutostart,
  setConfirmCloseRunningTerminal,
  setDefaultWorkspaceEnv,
  setExplorerGitDecorations,
  setShowHidden,
  setTerminalCursorBlink,
  setTerminalCursorStyle,
  setTerminalFontFamily,
  setTerminalFontSize,
  setTerminalFontWeight,
  setTerminalLetterSpacing,
  setTerminalScrollback,
  setTerminalMacOptionAsAlt,
  setTerminalScrollSpeed,
  setTerminalShell,
  coerceTerminalRenderer,
  setTerminalRenderer,
  setTerminalScreenReader,
  setZoomLevel,
  TERMINAL_FONT_SIZES,
  TERMINAL_SCROLLBACK_PRESETS,
} from "@/modules/settings/store";
import { coerceMacOptionAsAlt } from "@/modules/terminal/ghostty/input/macOptionAsAlt";
import { useTheme } from "@/modules/theme";
import {
  ComputerIcon,
  Moon02Icon,
  Sun03Icon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { invoke } from "@/host/core";
import { disable, enable, isEnabled } from "@/host/autostart";
import { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import { createStartupService, type StartupService } from "../startupController";
import { errorText } from "../../../../src/displayError";
import { SectionHeader } from "../components/SectionHeader";
import { SettingRow } from "../components/SettingRow";

const APPEARANCE: {
  id: ThemePref;
  label: string;
  icon: typeof ComputerIcon;
}[] = [
  { id: "system", label: "System", icon: ComputerIcon },
  { id: "light", label: "Light", icon: Sun03Icon },
  { id: "dark", label: "Dark", icon: Moon02Icon },
];

const TERMINAL_FONT_WEIGHTS = [
  { value: "normal", label: "Normal" },
  { value: "500", label: "Medium" },
  { value: "600", label: "Semi-Bold" },
  { value: "bold", label: "Bold" },
] as const;
const TERMINAL_CURSOR_STYLES = [
  { value: "bar", label: "Bar" },
  { value: "block", label: "Block" },
  { value: "underline", label: "Underline" },
] as const;
const LETTER_SPACINGS = [-4, -3, -2, -1, 0, 1, 2, 3, 4] as const;
const OPTION_AS_ALT_CHOICES = [
  ["off", "Off"],
  ["left", "Left Option"],
  ["right", "Right Option"],
  ["both", "Both"],
] as const;
const SCROLL_SPEEDS = [0.25, 0.5, 0.75, 1, 1.5, 2, 3, 4] as const;

type ShellInfo = { name: string; path: string; integrated: boolean };
const SHELL_AUTO = "auto";
const ZOOM_MIN = 0.5;
const ZOOM_MAX = 2.0;
const ZOOM_STEP = 0.05;
const NOTIFICATION_TEST_DELAY_MS = 2_000;

type NotificationTestState =
  | OsNotificationResult
  | "idle"
  | "waiting"
  | "sending";

export function GeneralSection() {
  const { mode, setMode } = useTheme();
  const reducedMotion = useSyncExternalStore(subscribeReducedMotionPreference, getReducedMotionPreference, getReducedMotionPreference);
  const [motionError, setMotionError] = useState("");

  const [startupController] = useState(() => {
    const host = (window.parent as unknown as { __JARVIS_NATIVE__?: { startup?: StartupService } }).__JARVIS_NATIVE__;
    return host?.startup ?? createStartupService({
      read: isEnabled,
      change: next => next ? enable() : disable(),
      persist: setAutostart,
    });
  });
  const subscribeStartup = useCallback((onChange: () => void) => {
    const unsubscribe = startupController.subscribe(onChange);
    // Removing the settings iframe may destroy its realm without a React unmount.
    window.addEventListener("pagehide", unsubscribe, { once: true });
    return () => { window.removeEventListener("pagehide", unsubscribe); unsubscribe(); };
  }, [startupController]);
  const startup = useSyncExternalStore(subscribeStartup, startupController.getSnapshot, startupController.getSnapshot);
  const showHidden = usePreferencesStore((s) => s.showHidden);
  const explorerGitDecorations = usePreferencesStore(
    (s) => s.explorerGitDecorations,
  );
  const terminalRenderer = usePreferencesStore((s) => s.terminalRenderer);
  const terminalScreenReader = usePreferencesStore(
    (s) => s.terminalScreenReader,
  );
  const terminalCursorBlink = usePreferencesStore((s) => s.terminalCursorBlink);
  const terminalCursorStyle = usePreferencesStore((s) => s.terminalCursorStyle);
  const terminalFontFamily = usePreferencesStore((s) => s.terminalFontFamily);
  const terminalFontWeight = usePreferencesStore((s) => s.terminalFontWeight);
  const terminalShell = usePreferencesStore((s) => s.terminalShell);
  const [shells, setShells] = useState<ShellInfo[]>([]);
  const [wslDistros, setWslDistros] = useState<{ name: string }[]>([]);
  const defaultWorkspaceEnv = usePreferencesStore((s) => s.defaultWorkspaceEnv);
  const terminalLetterSpacing = usePreferencesStore(
    (s) => s.terminalLetterSpacing,
  );
  const terminalFontSize = usePreferencesStore((s) => s.terminalFontSize);
  const terminalScrollback = usePreferencesStore((s) => s.terminalScrollback);
  const terminalScrollSpeed = usePreferencesStore((s) => s.terminalScrollSpeed);
  const terminalMacOptionAsAlt = usePreferencesStore(
    (s) => s.terminalMacOptionAsAlt,
  );
  const confirmCloseRunningTerminal = usePreferencesStore(
    (s) => s.confirmCloseRunningTerminal,
  );
  const zoomLevel = usePreferencesStore((s) => s.zoomLevel);
  const agentNotifications = usePreferencesStore((s) => s.agentNotifications);
  const agentNotificationSound = usePreferencesStore(
    (s) => s.agentNotificationSound,
  );
  const [notificationTest, setNotificationTest] =
    useState<NotificationTestState>("idle");
  const notificationTestPending =
    notificationTest === "waiting" || notificationTest === "sending";

  const testNotification = async () => {
    setNotificationTest("waiting");
    await new Promise((resolve) =>
      setTimeout(resolve, NOTIFICATION_TEST_DELAY_MS),
    );
    setNotificationTest("sending");
    setNotificationTest(await testAgentOsNotification(agentNotificationSound));
  };

  useEffect(() => { void startupController.refresh(); }, [startupController]);

  useEffect(() => {
    void invoke<ShellInfo[]>("pty_list_shells")
      .then(setShells)
      .catch(() => {});
    void invoke<{ name: string }[]>("wsl_list_distros")
      .then(setWslDistros)
      .catch(() => {});
  }, []);

  return (
    <div className="flex flex-col gap-6">
      <LanguagePicker />
      <SectionHeader
        title={t("General")}
        description={t("Mode, terminal, and startup.")}
      />

      <div className="flex flex-col gap-2">
        <Label>{t("Appearance")}</Label>
        <div className="grid grid-cols-3 gap-2">
          {APPEARANCE.map((o) => (
            <button
              key={o.id}
              type="button"
              onClick={() => setMode(o.id)}
              className={cn(
                "group flex h-20 flex-col items-center justify-center gap-1.5 rounded-lg border bg-card transition-all",
                mode === o.id
                  ? "border-foreground/60 ring-1 ring-foreground/20"
                  : "border-border/60 hover:border-border",
              )}
            >
              <HugeiconsIcon icon={o.icon} size={18} strokeWidth={1.5} />
              <span className="text-[11.5px]">{t(o.label)}</span>
            </button>
          ))}
        </div>
        <p className="text-[11px] text-muted-foreground">
          {t("For theme, background and customization, see the")}{" "}
          <strong className="font-medium text-foreground">{t("Themes")}</strong>{" "}
          {t("tab.")}{" "}
        </p>
      </div>

      <SettingRow
        title={t("Reduce Animations")}
        description={t("Use less motion in Jarvis. Your system's reduced-motion preference always applies.")}
      >
        <Switch
          aria-label={t("Reduce Animations")}
          checked={reducedMotion}
          onCheckedChange={(value) => {
            try { setReducedMotionPreference(value); setMotionError(""); }
            catch (error) { setMotionError(String(error)); }
          }}
        />
      </SettingRow>
      {motionError && <p role="alert" className="text-xs text-destructive">{t("Could not save the motion preference: {0}", motionError)}</p>}

      <div className="flex flex-col gap-2">
        <Label>{t("Zoom")}</Label>
        <div className="flex flex-col gap-3 rounded-lg border border-border/60 p-3">
          <div className="flex items-center justify-between gap-3">
            <span className="text-[11.5px] text-muted-foreground">
              {t("UI zoom level")}{" "}
            </span>
            <span className="tabular-nums text-[11px] text-muted-foreground">
              {Math.round(zoomLevel * 100)}%
            </span>
          </div>
          <Slider
            value={[zoomLevel]}
            min={ZOOM_MIN}
            max={ZOOM_MAX}
            step={ZOOM_STEP}
            onValueChange={(v) => void setZoomLevel(v[0] ?? 1)}
          />
        </div>
      </div>

      <div className="flex flex-col gap-2">
        <Label>{t("Explorer")}</Label>
        <SettingRow
          title={t("Show hidden files")}
          description={t(
            "Include dot-prefixed files and folders (.env, .gitignore, .config) in the file explorer and search.",
          )}
        >
          <Switch
            checked={showHidden}
            onCheckedChange={(v) => void setShowHidden(v)}
          />
        </SettingRow>
        <SettingRow
          title={t("Git decorations")}
          description={t(
            "Tint changed files and dim gitignored entries in the file explorer.",
          )}
        >
          <Switch
            checked={explorerGitDecorations}
            onCheckedChange={(v) => void setExplorerGitDecorations(v)}
          />
        </SettingRow>
      </div>

      <div className="flex flex-col gap-2">
        <Label>{t("Terminal")}</Label>
        <SettingRow
          title={t("Terminal renderer")}
          description={t("Automatic and WebGPU draw with WebGPU and fall back to WebGL where the webview has none. WebGL is a compatibility mode: it uses more CPU and holds at most 12 visible panes per window. Applies to new terminals.")}
        >
          <Select
            value={terminalRenderer}
            onValueChange={(value) =>
              void setTerminalRenderer(coerceTerminalRenderer(value))
            }
          >
            <SelectTrigger className="h-8 w-36 text-[12px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="auto">{t("Automatic")}</SelectItem>
              <SelectItem value="webgpu">{t("WebGPU")}</SelectItem>
              <SelectItem value="webgl">{t("WebGL")}</SelectItem>
            </SelectContent>
          </Select>
        </SettingRow>
        <SettingRow
          title={t("Screen reader support")}
          description={t(
            "Expose terminal output as accessible text. Page Up and Page Down browse history when the output region is focused.",
          )}
        >
          <Switch
            checked={terminalScreenReader}
            onCheckedChange={(value) => void setTerminalScreenReader(value)}
          />
        </SettingRow>
        <SettingRow
          title={t("Cursor blinking")}
          description={t(
            "Blink the terminal cursor. Off by default for lower idle CPU, matching VS Code and the macOS terminal.",
          )}
        >
          <Switch
            checked={terminalCursorBlink}
            onCheckedChange={(v) => void setTerminalCursorBlink(v)}
          />
        </SettingRow>
        <SettingRow
          title={t("Cursor style")}
          description={t("Shape of the terminal cursor.")}
        >
          <Select
            value={terminalCursorStyle}
            onValueChange={(v) => void setTerminalCursorStyle(v)}
          >
            <SelectTrigger
              value={terminalCursorStyle}
              className="h-8 w-28 text-[12px]"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {TERMINAL_CURSOR_STYLES.map((style) => (
                <SelectItem
                  key={style.value}
                  value={style.value}
                  className="text-[12px]"
                >
                  {style.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </SettingRow>
        <FontFamilyInput
          value={terminalFontFamily}
          onCommit={(v) => void setTerminalFontFamily(v)}
        />
        <SettingRow
          title={t("Font weight")}
          description={t("Thickness of terminal characters")}
        >
          <Select
            value={terminalFontWeight}
            onValueChange={(v) => void setTerminalFontWeight(v)}
          >
            <SelectTrigger
              value={terminalFontWeight}
              className="h-8 w-28 text-[12px]"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {TERMINAL_FONT_WEIGHTS.map((w) => (
                <SelectItem
                  key={w.value}
                  value={w.value}
                  className="text-[12px]"
                >
                  {w.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </SettingRow>
        <SettingRow
          title={t("Integrated terminal shell")}
          description={
            shells.find((s) => s.path === terminalShell)?.integrated === false
              ? "Command blocks and directory tracking are unavailable for this shell."
              : wslDistros.length > 0
                ? "Shell for the integrated terminal. WSL spaces use the distro login shell. Existing tabs keep their shell."
                : "Shell for new terminal tabs. Existing tabs keep their shell."
          }
        >
          <Select
            value={terminalShell || SHELL_AUTO}
            onValueChange={(v) =>
              void setTerminalShell(v === SHELL_AUTO ? "" : v)
            }
          >
            <SelectTrigger
              value={terminalShell || SHELL_AUTO}
              className="h-8 w-40 text-[12px]"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={SHELL_AUTO} className="text-[12px]">
                {t("Auto")}{" "}
              </SelectItem>
              {shells.map((s) => (
                <SelectItem key={s.path} value={s.path} className="text-[12px]">
                  {s.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </SettingRow>
        {(wslDistros.length > 0 || defaultWorkspaceEnv !== "local") && (
          <SettingRow
            title={t("Workspace environment")}
            description={t(
              "Where new spaces run, terminal and AI agent alike: Windows or a WSL distro. Existing spaces keep theirs; switch any from the status bar.",
            )}
          >
            <Select
              value={defaultWorkspaceEnv}
              onValueChange={(v) => void setDefaultWorkspaceEnv(v)}
            >
              <SelectTrigger
                value={defaultWorkspaceEnv}
                className="h-8 w-40 text-[12px]"
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="local" className="text-[12px]">
                  {t("Windows")}{" "}
                </SelectItem>
                {wslDistros.map((d) => (
                  <SelectItem
                    key={d.name}
                    value={`wsl:${d.name}`}
                    className="text-[12px]"
                  >
                    {t("WSL:")} {d.name}
                  </SelectItem>
                ))}
                {defaultWorkspaceEnv.startsWith("wsl:") &&
                  !wslDistros.some(
                    (d) => `wsl:${d.name}` === defaultWorkspaceEnv,
                  ) && (
                    <SelectItem
                      value={defaultWorkspaceEnv}
                      className="text-[12px]"
                    >
                      {defaultWorkspaceEnv.slice("wsl:".length)}{" "}
                      {t("(unavailable)")}{" "}
                    </SelectItem>
                  )}
              </SelectContent>
            </Select>
          </SettingRow>
        )}
        <SettingRow
          title={t("Letter spacing")}
          description={t(
            "Extra horizontal space between characters (px). Use negative values to tighten Nerd Fonts.",
          )}
        >
          <Select
            value={String(terminalLetterSpacing)}
            onValueChange={(v) => void setTerminalLetterSpacing(Number(v))}
          >
            <SelectTrigger size="sm" className="h-8 w-28 text-[12px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {LETTER_SPACINGS.map((v) => (
                <SelectItem key={v} value={String(v)} className="text-[12px]">
                  {v > 0 ? `+${v}` : v} {t("px")}{" "}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </SettingRow>
        <SettingRow
          title={t("Font size")}
          description={t("Terminal text size.")}
        >
          <Select
            value={String(terminalFontSize)}
            onValueChange={(v) => void setTerminalFontSize(Number(v))}
          >
            <SelectTrigger size="sm" className="h-8 w-28 text-[12px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {TERMINAL_FONT_SIZES.map((size) => (
                <SelectItem
                  key={size}
                  value={String(size)}
                  className="text-[12px]"
                >
                  {size} {t("px")}{" "}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </SettingRow>
        <SettingRow
          title={t("Scrollback")}
          description={t(
            "Lines of history kept per terminal. Higher uses more RAM (~3 KB / line).",
          )}
        >
          <Select
            value={String(terminalScrollback)}
            onValueChange={(v) => void setTerminalScrollback(Number(v))}
          >
            <SelectTrigger size="sm" className="h-8 w-36 text-[12px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {TERMINAL_SCROLLBACK_PRESETS.map((lines) => (
                <SelectItem
                  key={lines}
                  value={String(lines)}
                  className="text-[12px]"
                >
                  {lines.toLocaleString()} {t("lines")}{" "}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </SettingRow>
        {IS_MAC && (
          <SettingRow
            title={t("Option as Alt")}
            description={t("Send Option as Alt (Meta) so readline and editor shortcuts like Option+B and Option+F work. Off types the characters Option produces. Keys are matched by their position on a US keyboard, so layouts that type other letters on those keys are not followed.")}
          >
            <Select
              value={terminalMacOptionAsAlt}
              onValueChange={(v) =>
                void setTerminalMacOptionAsAlt(coerceMacOptionAsAlt(v))
              }
            >
              <SelectTrigger size="sm" className="h-8 w-36 text-[12px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {OPTION_AS_ALT_CHOICES.map(([value, label]) => (
                  <SelectItem key={value} value={value} className="text-[12px]">
                    {label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </SettingRow>
        )}
        <SettingRow
          title={t("Scroll speed")}
          description={t("Lines per wheel or trackpad movement, for history and for programs that read the mouse wheel.")}
        >
          <Select
            value={String(terminalScrollSpeed)}
            onValueChange={(v) => void setTerminalScrollSpeed(Number(v))}
          >
            <SelectTrigger size="sm" className="h-8 w-28 text-[12px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {(SCROLL_SPEEDS.some((v) => v === terminalScrollSpeed)
                ? SCROLL_SPEEDS
                : [...SCROLL_SPEEDS, terminalScrollSpeed].sort((a, b) => a - b)
              ).map((v) => (
                <SelectItem key={v} value={String(v)} className="text-[12px]">
                  {v}×
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </SettingRow>
        <SettingRow
          title={t("Confirm before killing a running process")}
          description={t(
            "Ask before closing a terminal tab or quitting while a command is still running. Unsaved editor changes are always confirmed.",
          )}
        >
          <Switch
            checked={confirmCloseRunningTerminal}
            onCheckedChange={(v) => void setConfirmCloseRunningTerminal(v)}
          />
        </SettingRow>
      </div>

      <div className="flex flex-col gap-2">
        <Label>{t("Agents")}</Label>
        <SettingRow
          title={t("Coding agent notifications")}
          description={t(
            "Alert when a coding agent needs your input or finishes. Native notification when Jarvis is unfocused, in-app otherwise.",
          )}
        >
          <div className="flex items-center gap-2">
            <Button
              type="button"
              variant="outline"
              size="xs"
              disabled={!agentNotifications || notificationTestPending}
              title={notificationTestTitle(notificationTest)}
              onClick={() => void testNotification()}
            >
              {notificationTestLabel(notificationTest)}
            </Button>
            <Switch
              checked={agentNotifications}
              disabled={notificationTestPending}
              onCheckedChange={(v) => {
                setNotificationTest("idle");
                void setAgentNotifications(v);
              }}
            />
          </div>
        </SettingRow>
        <SettingRow
          title={t("Notification sound")}
          description={t(
            "Play a sound with agent notifications and in-app alerts.",
          )}
        >
          <Switch
            checked={agentNotificationSound}
            disabled={!agentNotifications || notificationTestPending}
            onCheckedChange={(v) => void setAgentNotificationSound(v)}
          />
        </SettingRow>
      </div>

      <div className="flex flex-col gap-2">
          <Label>{t("Startup")}</Label>
          <div className="flex flex-col gap-2">
            <SettingRow
              title={t("Launch at login")}
              description={t("Open Jarvis automatically when you sign in.")}
            >
              <Switch
                aria-label={t("Launch at login")}
                checked={startup.enabled ?? false}
                disabled={startup.busy || startup.enabled === null}
                onCheckedChange={(value) => void startupController.setEnabled(value)}
              />
            </SettingRow>
            {startup.busy && <p role="status">{t("Checking startup setting…")}</p>}
            {startup.error && <p role="alert">{errorText(startup.error)}</p>}
            {startup.error && <Button variant="outline" disabled={startup.busy} onClick={() => void startupController.refresh()}>{t("Retry startup check")}</Button>}
          </div>
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

function notificationTestLabel(status: NotificationTestState): string {
  switch (status) {
    case "waiting":
      return t("Switch apps...");
    case "sending":
      return t("Sending...");
    case "requested":
      return t("Requested");
    case "denied":
      return t("Blocked");
    case "failed":
      return t("Failed");
    default:
      return t("Test in 2s");
  }
}

function notificationTestTitle(status: NotificationTestState): string {
  switch (status) {
    case "waiting":
      return t("Switch to another app to verify native delivery");
    case "requested":
      return t("The native notification was requested");
    case "denied":
      return t("Notifications are disabled by the system");
    case "failed":
      return t("Jarvis could not request a native notification");
    default:
      return t("Send a native test notification after two seconds");
  }
}

function FontFamilyInput({
  value,
  onCommit,
}: {
  value: string;
  onCommit: (v: string) => void;
}) {
  const [draft, setDraft] = useState(value);

  useEffect(() => {
    setDraft(value);
  }, [value]);

  // Commit (and trim) only on blur/Enter so a trailing space can be typed
  // mid-edit, e.g. "JetBrains Mono ".
  const commit = () => {
    const next = draft.trim();
    if (next !== draft) setDraft(next);
    if (next !== value) onCommit(next);
  };

  return (
    <SettingRow
      title={t("Font family")}
      description={t("Terminal font (e.g. \"CaskaydiaCove Nerd Font Mono\"). Icons render without a Nerd Font; leave blank to use an installed one when found.")}
    >
      <input
        type="text"
        value={draft}
        placeholder={t("Auto-detect")}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") e.currentTarget.blur();
        }}
        className="h-8 w-48 rounded-md border border-border bg-background px-2.5 text-[12px] outline-none focus:border-foreground/40"
      />
    </SettingRow>
  );
}
