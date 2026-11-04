import type { ApiError } from "../api/errors";
import type { Language } from "../api/legacy";
import { en } from "./en";
import { ja, type MessageKey } from "./ja";

export type { MessageKey };
export const LANGUAGES: readonly Language[] = ["ja", "en"];
const catalogs: Record<Language, Record<MessageKey, string>> = { ja, en };

export function translate(language: Language, key: MessageKey, params: Record<string, string | number> = {}): string {
  const template = catalogs[language][key] ?? ja[key] ?? key;
  return template.replace(/\{(\w+)\}/g, (match, name: string) => (name in params ? String(params[name]) : match));
}

function codeKey(code: string): MessageKey | null {
  const key = `code.${code}`;
  return key in ja ? (key as MessageKey) : null;
}

/** Fixed code → text; unknown codes fall back to the kind's text (the code stays visible). */
export function errorMessage(language: Language, error: ApiError): string {
  if (error.outcomeUnknown) return translate(language, "error.outcomeUnknown");
  const byCode = error.code ? codeKey(error.code) : null;
  return translate(language, byCode ?? (`error.${error.kind}` as MessageKey));
}
