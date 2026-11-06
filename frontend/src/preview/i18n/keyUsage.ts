/**
 * Static key-usage scan for the prototype texts (iteration 5, owner memo #29). Used by the audit
 * test: every key in ja.ts must be referenced from the prototype source, either literally
 * ("wizard.approve"), as an environment-fact value ("key:env.value.broken") or through a template prefix (`stage.${id}` covers every "stage.*" key).
 * Pure function over file contents, so it needs no file-system access itself.
 */
export function referencedKeys(keys: readonly string[], sources: readonly string[]): { used: Set<string>; prefixes: string[] } {
  const text = sources.join("\n");
  const prefixes = new Set<string>();
  for (const m of text.matchAll(/`([a-zA-Z][\w.-]*\.)\$\{/g)) prefixes.add(m[1]!);
  const used = new Set<string>();
  for (const k of keys) {
    // Environment facts carry i18n keys as values: "key:env.value.broken".
    if (text.includes(`"${k}"`) || text.includes(`'${k}'`) || text.includes(`\`${k}\``) || text.includes(`"key:${k}"`)) used.add(k);
    else if ([...prefixes].some((p) => k.startsWith(p))) used.add(k);
  }
  return { used, prefixes: [...prefixes].sort() };
}
