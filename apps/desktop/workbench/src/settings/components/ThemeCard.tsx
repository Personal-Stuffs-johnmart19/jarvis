import { Edit02Icon, Delete02Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useLocale } from "../../../../src/i18n";
import { t } from "../../../../src/localization";
import { cn } from "@/lib/utils";
import type { Theme } from "@/modules/theme/types";

export function ThemeCard({ theme, mode, selected, custom, onSelect, onEdit, onRemove }: {
  theme: Theme;
  mode: "light" | "dark";
  selected: boolean;
  custom: boolean;
  onSelect: () => void;
  onEdit: () => void;
  onRemove: () => void;
}) {
  useLocale();
  const colors = (theme.variants[mode] ?? theme.variants.dark ?? theme.variants.light)?.colors;
  return <div className={cn("group flex min-w-0 items-center gap-1 rounded-lg border transition-colors",
    selected ? "border-foreground/60 ring-1 ring-foreground/20" : "border-border/60 hover:border-border")}
    data-theme-card={theme.id}>
    <button type="button" aria-pressed={selected} onClick={onSelect}
      className="flex min-w-0 flex-1 items-center gap-3 rounded-lg p-2.5 text-left focus-visible:outline-2 focus-visible:outline-ring">
      <span aria-hidden="true" className="flex h-10 w-14 shrink-0 items-center justify-center gap-1 rounded-md border border-border/40" style={{ background: colors?.background ?? "var(--background)" }}>
        <span className="h-5 w-2 rounded-sm" style={{ background: colors?.primary ?? colors?.accent ?? "var(--accent)" }} />
        <span className="h-5 w-2 rounded-sm" style={{ background: colors?.foreground ?? "var(--foreground)", opacity: 0.7 }} />
        <span className="h-5 w-2 rounded-sm" style={{ background: colors?.muted ?? "var(--muted)" }} />
      </span>
      <span className="flex min-w-0 flex-1 flex-col">
        <span className="truncate text-[12.5px] font-medium">{theme.name}</span>
        {theme.description && <span className="truncate text-[11px] text-muted-foreground">{custom ? theme.description : t(theme.description)}</span>}
      </span>
    </button>
    {custom && <div className="flex shrink-0 flex-col gap-0.5 pr-1">
      <button type="button" aria-label={t("Edit {0}", theme.name)} onClick={onEdit}
        className="inline-flex h-6 w-6 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring">
        <HugeiconsIcon icon={Edit02Icon} size={12} strokeWidth={1.75} aria-hidden="true" />
      </button>
      <button type="button" aria-label={t("Remove {0}", theme.name)} onClick={onRemove}
        className="inline-flex h-6 w-6 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-destructive focus-visible:outline-2 focus-visible:outline-ring">
        <HugeiconsIcon icon={Delete02Icon} size={12} strokeWidth={1.75} aria-hidden="true" />
      </button>
    </div>}
  </div>;
}
