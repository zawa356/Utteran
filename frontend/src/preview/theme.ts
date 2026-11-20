export { ACCENT_SWATCH, blueDark, blueLight, buildTheme, greenDark, greenLight, type Accent } from "../styles/palette";

/** Vuetify theme names registered by preview/main.ts. */
export const PREVIEW_THEMES = {
  green: { light: "greenLight", dark: "greenDark" },
  blue: { light: "blueLight", dark: "blueDark" },
} as const;

/**
 * Prototype component defaults (iteration 3, readability first). The production defaults use
 * `density: "compact"` globally, which makes a `size="small"` button 16 px tall with 12 px text.
 * The prototype keeps compact fields and lists but gives buttons, tabs and chips their normal
 * height (buttons 36 px, icon buttons 40 px), so a target is never a tiny strip.
 */
export const previewDefaults = {
  global: { density: "compact" },
  VBtn: { variant: "flat", rounded: "sm", density: "default" },
  VCard: { variant: "outlined", rounded: "sm" },
  VAlert: { variant: "tonal", density: "compact" },
  VSelect: { variant: "outlined", density: "compact", hideDetails: "auto" },
  VList: { density: "compact" },
  VListItem: { density: "default" },
  VTextField: { variant: "outlined", density: "compact" },
  VTabs: { density: "default" },
  VChip: { density: "default", size: "small" },
} as const;
