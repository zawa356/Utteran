import type { ThemeDefinition } from "vuetify";
import type { Accent } from "../domain/shell";
export type { Accent } from "../domain/shell";

/** The Design Freeze palettes shared by the product and prototype. */
type Mode = "light" | "dark";

const SEMANTIC: Record<Mode, Record<"error" | "warning" | "info" | "success", string>> = {
  light: { error: "#b3261e", warning: "#8a5a00", info: "#3f5f7a", success: "#4f6400" },
  dark: { error: "#f2b8b5", warning: "#e8c27a", info: "#a9c4dc", success: "#c8d98a" },
};

interface AccentColours {
  background: string;
  surface: string;
  surfaceVariant: string;
  onSurfaceVariant: string;
  onSurface: string;
  primary: string;
  onPrimary: string;
  secondary: string;
  onSecondary: string;
}

const ACCENTS: Record<Accent, Record<Mode, AccentColours>> = {
  green: {
    light: { background: "#f5f7f6", surface: "#ffffff", surfaceVariant: "#e6ebe8", onSurfaceVariant: "#2b332f", onSurface: "#1c2420", primary: "#1f6b4f", onPrimary: "#ffffff", secondary: "#55605a", onSecondary: "#ffffff" },
    dark: { background: "#131715", surface: "#1b201e", surfaceVariant: "#28302c", onSurfaceVariant: "#dfe6e2", onSurface: "#e6ece9", primary: "#7fd1ad", onPrimary: "#0b2a1d", secondary: "#aab5ae", onSecondary: "#161b19" },
  },
  // The UI-2 hue (src/styles/theme.ts primary #2f5d8a / #8db4dc) on UI-2's neutrals.
  blue: {
    light: { background: "#f5f6f8", surface: "#ffffff", surfaceVariant: "#e8eaee", onSurfaceVariant: "#2a2f36", onSurface: "#1b2027", primary: "#2f5d8a", onPrimary: "#ffffff", secondary: "#56606b", onSecondary: "#ffffff" },
    dark: { background: "#16181c", surface: "#1e2126", surfaceVariant: "#2a2e35", onSurfaceVariant: "#dde2e8", onSurface: "#e5e8ec", primary: "#8db4dc", onPrimary: "#0d2236", secondary: "#a9b2bc", onSecondary: "#16191d" },
  },
};

export function buildTheme(accent: Accent, mode: Mode): ThemeDefinition {
  const a = ACCENTS[accent][mode];
  return {
    dark: mode === "dark",
    colors: {
      background: a.background,
      surface: a.surface,
      "surface-variant": a.surfaceVariant,
      "on-surface-variant": a.onSurfaceVariant,
      "on-background": a.onSurface,
      "on-surface": a.onSurface,
      primary: a.primary,
      "on-primary": a.onPrimary,
      secondary: a.secondary,
      "on-secondary": a.onSecondary,
      ...SEMANTIC[mode],
    },
    variables:
      mode === "light"
        ? // Vuetify's 0.6 gives ~4.2:1 for secondary text on the background; 0.72 gives ≥ 6:1.
          { "border-color": a.onSurface, "border-opacity": 0.16, "medium-emphasis-opacity": 0.72 }
        : { "border-color": a.onSurface, "border-opacity": 0.2 },
  };
}

/** Swatch colours for the theme picker (taken from the theme itself, not repeated per page). */
export const ACCENT_SWATCH: Record<Accent, string> = { green: ACCENTS.green.light.primary, blue: ACCENTS.blue.light.primary };

export const greenLight = buildTheme("green", "light");
export const greenDark = buildTheme("green", "dark");
export const blueLight = buildTheme("blue", "light");
export const blueDark = buildTheme("blue", "dark");
