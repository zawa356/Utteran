import type { PreviewLanguage } from "../domain/demo";
import { en } from "./en";
import { ja, type PreviewKey } from "./ja";

/** Same dictionary scheme as src/i18n (typed ja keys, en must match), kept separate so no
 * prototype text can end up in the production bundle. */
export type { PreviewKey };
const catalogs: Record<PreviewLanguage, Record<PreviewKey, string>> = { ja, en };

export function translatePreview(language: PreviewLanguage, key: PreviewKey, params: Record<string, string | number> = {}): string {
  const template = catalogs[language][key] ?? ja[key] ?? key;
  return template.replace(/\{(\w+)\}/g, (match, name: string) => (name in params ? String(params[name]) : match));
}

export function isPreviewKey(key: string): key is PreviewKey {
  return key in ja;
}
