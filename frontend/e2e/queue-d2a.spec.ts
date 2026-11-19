import type { Page, Request } from "@playwright/test";
import { expect, test } from "./fixtures";

/**
 * Bundle D2-A in the `--next-ui` (legacy) shape: the queue page is NOT migrated there. The Expert
 * "Queue" keeps the not-migrated notice, the sidebar says "not migrated", nothing looks like a queue,
 * and no `/api/v1` request is ever made. Headless Chromium — NOT a WebView2 acceptance.
 */

function recordApi(page: Page): Request[] {
  const seen: Request[] = [];
  page.on("request", (r) => {
    if (new URL(r.url()).pathname.startsWith("/api/")) seen.push(r);
  });
  return seen;
}
const line = (r: Request) => `${r.method()} ${new URL(r.url()).pathname}`;

test("D2A-01 (legacy): Expert Queue stays not migrated; no queue table, no /api/v1 request, no write", async ({ app }) => {
  const seen = recordApi(app);
  await expect(app.getByTestId("preview-notice")).toHaveAttribute("data-notice", "app.previewNotice");
  // Normal offers no queue destination.
  await expect(app.getByTestId("nav-queue")).toHaveCount(0);
  await app.getByTestId("mode-expert").click();
  await expect(app.getByTestId("nav-queue")).toContainText("未移行");
  await app.getByTestId("nav-queue").click();
  await expect(app.getByTestId("content")).toHaveAttribute("data-section", "queue");
  await expect(app.getByTestId("section-queue").getByTestId("not-migrated")).toBeVisible();
  for (const id of ["queue-table", "queue-row", "queue-detail", "queue-search", "queue-filter", "queue-sort", "queue-reload"]) await expect(app.getByTestId(id)).toHaveCount(0);
  await expect(app.getByTestId("section-queue").locator("button")).toHaveCount(0);
  await app.waitForTimeout(2500);
  expect(seen.map(line).filter((l) => l.includes(" /api/v1"))).toEqual([]);
  expect(seen.filter((r) => r.method() !== "GET").map(line)).toEqual([]);
});
