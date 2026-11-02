import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

const root = new URL('../dist/', import.meta.url);
const pages = ['index', 'getting-started', 'features', 'hardware', 'privacy', 'faq'];
for (const locale of ['', 'en/']) {
  for (const slug of pages) {
    const path = new URL(`${locale}${slug === 'index' ? '' : `${slug}/`}index.html`, root);
    if (!existsSync(path)) {
      throw new Error(`Missing generated page: ${path.pathname}`);
    }
    const html = readFileSync(path, 'utf8');
    const lang = locale ? 'en' : 'ja';
    if (!html.includes(`lang="${lang}"`)) {
      throw new Error(`Wrong language for ${path.pathname}: expected ${lang}`);
    }
  }
}
console.log('Japanese and English site pages generated successfully.');
