/** WCAG 2.x relative luminance / contrast ratio for #rrggbb colours (prototype palette checks). */
function channel(v: number): number {
  const c = v / 255;
  return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}

function rgb(hex: string): [number, number, number] {
  const m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex);
  if (!m) throw new Error(`not a #rrggbb colour: ${hex}`);
  return [parseInt(m[1]!, 16), parseInt(m[2]!, 16), parseInt(m[3]!, 16)];
}

export function luminance(hex: string): number {
  const [r, g, b] = rgb(hex);
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

export function contrastRatio(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

/** `fg` drawn with `alpha` opacity over an opaque `bg` (how Vuetify draws emphasis/borders/tints). */
export function blend(fg: string, bg: string, alpha: number): string {
  const f = rgb(fg);
  const b = rgb(bg);
  const mix = f.map((v, i) => Math.round(v * alpha + b[i]! * (1 - alpha)));
  return `#${mix.map((v) => v.toString(16).padStart(2, "0")).join("")}`;
}
