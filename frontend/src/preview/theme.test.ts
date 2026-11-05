import { describe, expect, it } from "vitest";
import { blend, contrastRatio } from "./domain/contrast";
import { greenDark, greenLight } from "./theme";

/**
 * WCAG 2.2 AA checks of the prototype "Utteran green" palette (1.4.3 text 4.5:1, 1.4.11 non-text
 * 3:1). The numbers are recorded in docs/architecture/ui3/iteration-2.md. How Vuetify draws them:
 * text = on-surface at the emphasis opacity; tonal chips/alerts = the colour on a 12 % tint of it;
 * selected rows = on-surface on a 12 % primary tint; outlined fields = on-surface at the field
 * border opacity (raised to 0.6 in preview.css); focus ring = primary.
 */
const THEMES = [
  { name: "light", t: greenLight, emphasis: { high: 0.87, medium: Number(greenLight.variables?.["medium-emphasis-opacity"]), disabled: 0.38 } },
  { name: "dark", t: greenDark, emphasis: { high: 1, medium: 0.7, disabled: 0.5 } },
] as const;
const SEMANTIC = ["primary", "secondary", "error", "warning", "info", "success"] as const;
const FIELD_BORDER_OPACITY = 0.6;
const TINT = 0.12;

function color(t: (typeof THEMES)[number]["t"], key: string): string {
  const value = t.colors?.[key];
  if (typeof value !== "string") throw new Error(`missing ${key}`);
  return value;
}

describe("prototype green palette — WCAG 2.2 AA", () => {
  for (const { name, t, emphasis } of THEMES) {
    const surface = color(t, "surface");
    const background = color(t, "background");
    const onSurface = color(t, "on-surface");

    it(`${name}: body and secondary text ≥ 4.5:1 on surface and background`, () => {
      for (const bg of [surface, background]) {
        expect(contrastRatio(blend(onSurface, bg, emphasis.high), bg)).toBeGreaterThanOrEqual(4.5);
        expect(contrastRatio(blend(onSurface, bg, emphasis.medium), bg)).toBeGreaterThanOrEqual(4.5);
      }
    });

    it(`${name}: brand and semantic colours used as text ≥ 4.5:1 (plain and on their tonal tint)`, () => {
      for (const key of SEMANTIC) {
        const c = color(t, key);
        expect(contrastRatio(c, surface), key).toBeGreaterThanOrEqual(4.5);
        expect(contrastRatio(c, background), key).toBeGreaterThanOrEqual(4.5);
        expect(contrastRatio(c, blend(c, surface, TINT)), `${key} tonal`).toBeGreaterThanOrEqual(4.5);
      }
    });

    it(`${name}: text on filled primary buttons and selected rows ≥ 4.5:1`, () => {
      expect(contrastRatio(color(t, "on-primary"), color(t, "primary"))).toBeGreaterThanOrEqual(4.5);
      const selectedRow = blend(color(t, "primary"), surface, TINT);
      expect(contrastRatio(blend(onSurface, selectedRow, emphasis.high), selectedRow)).toBeGreaterThanOrEqual(4.5);
    });

    it(`${name}: non-text UI (field outline, focus ring, resize handle) ≥ 3:1`, () => {
      expect(contrastRatio(blend(onSurface, surface, FIELD_BORDER_OPACITY), surface)).toBeGreaterThanOrEqual(3);
      expect(contrastRatio(color(t, "primary"), surface)).toBeGreaterThanOrEqual(3);
      expect(contrastRatio(color(t, "primary"), background)).toBeGreaterThanOrEqual(3);
      // The pane/sidebar separator grip uses on-surface at medium emphasis.
      expect(contrastRatio(blend(onSurface, background, emphasis.medium), background)).toBeGreaterThanOrEqual(3);
    });

    it(`${name}: the brand green is not the success colour (states also carry an icon and a label)`, () => {
      expect(color(t, "primary")).not.toBe(color(t, "success"));
      expect(contrastRatio(color(t, "primary"), color(t, "success"))).toBeGreaterThan(1);
    });

    it(`${name}: disabled text is recorded (WCAG exempts inactive controls; Vuetify default opacity)`, () => {
      const ratio = contrastRatio(blend(onSurface, surface, emphasis.disabled), surface);
      expect(ratio).toBeGreaterThan(1.8);
    });
  }
});
