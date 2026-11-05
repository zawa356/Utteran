import type { ThemeDefinition } from "vuetify";

/**
 * Prototype-only "Utteran green" proposal (UI-3-0 iteration 2). The production theme
 * (src/styles/theme.ts, UI-2 blue) is NOT changed; it stays selectable here for comparison.
 *
 * Rules: neutral backgrounds (white / light grey, dark grey); the deep green is limited to
 * selection, actions and accents. Semantic colours stay distinct from the brand hue: success is a
 * yellow-olive green, info a blue-grey, warning amber, error red — and every state is also shown
 * with an icon and a label, never by colour alone. Contrast values are asserted in theme.test.ts.
 */
export const greenLight: ThemeDefinition = {
  dark: false,
  colors: {
    background: "#f5f7f6",
    surface: "#ffffff",
    "surface-variant": "#e6ebe8",
    "on-surface-variant": "#2b332f",
    "on-background": "#1c2420",
    "on-surface": "#1c2420",
    primary: "#1f6b4f",
    "on-primary": "#ffffff",
    secondary: "#55605a",
    "on-secondary": "#ffffff",
    error: "#b3261e",
    warning: "#8a5a00",
    info: "#3f5f7a",
    success: "#4f6400",
  },
  variables: {
    "border-color": "#1c2420",
    "border-opacity": 0.16,
    // With this on-surface (#1c2420) Vuetify's 0.6 gives 4.2:1 for secondary text on the background; 0.72 gives 6.1:1.
    "medium-emphasis-opacity": 0.72,
  },
};

export const greenDark: ThemeDefinition = {
  dark: true,
  colors: {
    background: "#131715",
    surface: "#1b201e",
    "surface-variant": "#28302c",
    "on-surface-variant": "#dfe6e2",
    "on-background": "#e6ece9",
    "on-surface": "#e6ece9",
    primary: "#7fd1ad",
    "on-primary": "#0b2a1d",
    secondary: "#aab5ae",
    "on-secondary": "#161b19",
    error: "#f2b8b5",
    warning: "#e8c27a",
    info: "#a9c4dc",
    success: "#c8d98a",
  },
  variables: {
    "border-color": "#e6ece9",
    "border-opacity": 0.2,
  },
};

/** Vuetify theme names registered by preview/main.ts. */
export const PREVIEW_THEMES = {
  green: { light: "greenLight", dark: "greenDark" },
  ui2: { light: "light", dark: "dark" },
} as const;
