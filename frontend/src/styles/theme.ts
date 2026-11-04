import type { ThemeDefinition } from "vuetify";

/** Calm, low-saturation business palette; contrast checked against WCAG AA for text. */
export const lightTheme: ThemeDefinition = {
  dark: false,
  colors: {
    background: "#f5f6f8",
    surface: "#ffffff",
    "surface-variant": "#e8eaee",
    primary: "#2f5d8a",
    secondary: "#56606b",
    error: "#b3261e",
    warning: "#8a5a00",
    info: "#2f5d8a",
    success: "#2e6b3f",
  },
};

export const darkTheme: ThemeDefinition = {
  dark: true,
  colors: {
    background: "#16181c",
    surface: "#1e2126",
    "surface-variant": "#2a2e35",
    primary: "#8db4dc",
    secondary: "#a9b2bc",
    error: "#f2b8b5",
    warning: "#e8c27a",
    info: "#8db4dc",
    success: "#9fd3ab",
  },
};

/** Compact density everywhere; Material's large cards/elevation are avoided. */
export const componentDefaults = {
  global: { density: "compact" },
  VBtn: { variant: "flat", rounded: "sm" },
  VCard: { variant: "outlined", rounded: "sm" },
  VAlert: { variant: "tonal", density: "compact" },
  VSelect: { variant: "outlined", density: "compact", hideDetails: "auto" },
  VList: { density: "compact" },
  VTextField: { variant: "outlined", density: "compact" },
} as const;
