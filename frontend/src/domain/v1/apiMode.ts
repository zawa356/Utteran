/**
 * Which API the Vue UI may use, as the *server* wrote it into `/next/` (`utteran_gui.next_ui`):
 * `<meta name="utteran-api-mode" content="legacy|v1-dev">`. Only the exact word `v1-dev` enables
 * the S5-A v1 path; a missing tag, a leftover placeholder or any other value stays `legacy`.
 * Nothing in the URL, browser storage or the UI can switch it.
 */
export type ApiMode = "legacy" | "v1-dev";

export function apiModeFrom(content: string | null | undefined): ApiMode {
  return content === "v1-dev" ? "v1-dev" : "legacy";
}

export function readApiMode(doc: Pick<Document, "querySelectorAll">): ApiMode {
  const tags = doc.querySelectorAll<HTMLMetaElement>('meta[name="utteran-api-mode"]');
  // Exactly one tag, as the server renders it; duplicates are not trusted.
  if (tags.length !== 1) return "legacy";
  return apiModeFrom(tags[0]!.getAttribute("content"));
}
