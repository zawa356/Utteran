# Utteran website

> **Deployment status (updated 2026-11-02):** Pages is enabled and [run #41394884715](https://github.com/zawa356/Utteran/actions/runs/41394884715) deployed successfully from main `3fd187d`; `/Utteran/` and `/Utteran/en/` return HTTP 200. `/Utteran/favicon.svg` returns 404 (tracked separately as a minor site defect).
>
> History: PR #29 merged into main, and the 12-page build and Pages artifact upload passed. Initial deployment [run #41394333383](https://github.com/zawa356/Utteran/actions/runs/41394333383) failed with HTTP 404 because Pages has not been enabled. An administrator must select **Settings → Pages → Source: GitHub Actions**, rerun the failed deployment, and verify the Japanese/English public URLs. At that time the site was not yet public.

Official static website and user documentation for [Utteran](https://github.com/zawa356/Utteran).

- Framework: Astro 7 and the **Starlight** documentation theme.
- Default locale: Japanese at `/Utteran/`.
- English locale: `/Utteran/en/`.
- Deployment: GitHub Pages from `main`, using `.github/workflows/site.yml`.

## Development

Install Node.js 24 or later, then run:

```sh
cd site
npm ci
npm run dev
```

`npm run build` creates the static site and verifies that all Japanese and English page routes exist.

## Maintenance policy

Use Starlight's built-in splash template, Hero, Card, CardGrid, sidebar, search, language picker, and theme switcher. Do **not** reimplement these UI controls or introduce a single large custom page.

Each language has a corresponding Markdown/MDX page in `src/content/docs/`. Add or edit one topic per file and keep both translations in sync. Avoid custom CSS and client-side JavaScript unless a concrete, documented requirement cannot be met by Starlight.

Do not hand-copy development-only features into the public website. User-facing claims must match a published release. Download links point to GitHub Releases so that the site does not become a second distribution channel.

Astro's `site` and `base` settings intentionally target the repository Pages URL. When switching to a custom domain, update those settings and the explicit internal site links.

## GitHub Pages

After merging, open **Settings → Pages → Build and deployment** and set **Source: GitHub Actions**. The workflow builds on pull requests and deploys only from `main`. If Pages has not been enabled, deployment may fail until the source is configured.

The website currently links to the latest published release rather than displaying an unshipped development version.
