import type { Theme } from "../types";

// Mirrors the default CSS palette so the Jarvis host can share this theme.

export const teraxDefault: Theme = {
  id: "terax-default",
  name: "Jarvis Default",
  description: "Clean neutral greys with a full-color terminal.",
  editorTheme: { dark: "github-dark", light: "github-light" },
  variants: {
    light: {
      colors: {
        background: "#ffffff",
        foreground: "#111111",
        card: "#f3f3f3",
        cardForeground: "#111111",
        popover: "#ffffff",
        popoverForeground: "#111111",
        primary: "#111111",
        primaryForeground: "#ffffff",
        secondary: "#ededed",
        secondaryForeground: "#111111",
        muted: "#ededed",
        mutedForeground: "#656565",
        accent: "#e5e5e5",
        accentForeground: "#111111",
        border: "rgba(0, 0, 0, 0.09)",
        input: "rgba(0, 0, 0, 0.13)",
        ring: "#8a8a8a",
        sidebar: "#f3f3f3",
        sidebarForeground: "#111111",
      },
    },
    dark: {
      colors: {
        background: "#141414",
        foreground: "#f7f7f7",
        card: "#1d1d1d",
        cardForeground: "#f7f7f7",
        popover: "#1d1d1d",
        popoverForeground: "#f7f7f7",
        primary: "#f7f7f7",
        primaryForeground: "#141414",
        secondary: "#2a2a2a",
        secondaryForeground: "#f7f7f7",
        muted: "#1d1d1d",
        mutedForeground: "#a3a3a3",
        accent: "#2a2a2a",
        accentForeground: "#f7f7f7",
        border: "rgba(255, 255, 255, 0.09)",
        input: "rgba(255, 255, 255, 0.13)",
        ring: "#8a8a8a",
        sidebar: "#1d1d1d",
        sidebarForeground: "#f7f7f7",
      },
    },
  },
};
