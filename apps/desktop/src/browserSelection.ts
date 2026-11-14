export type BrowserProfile = { id: string; name: string; email?: string; token_configured?: boolean };
export type BrowserPreferences = { mode: string; profile: string };

// A controlled select must always agree with the profile sent to the backend.
// Preserve an explicit missing profile so it cannot silently receive another token.
export function browserSelection(preferences: BrowserPreferences, profiles: BrowserProfile[], current: string): BrowserPreferences {
  if (preferences.mode === "new") return { mode: "new", profile: "" };
  if (preferences.mode === "specific") return preferences;
  const profile = profiles.some(item => item.id === current) ? current : profiles[0]?.id ?? "";
  return { mode: "current", profile };
}
