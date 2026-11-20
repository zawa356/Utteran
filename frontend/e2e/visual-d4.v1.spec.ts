import { choose, expect } from "./fixtures";
import { test } from "./isolatedV1";

test("D4-B uses the chosen accent with the saved appearance and keeps narrow navigation reachable", async ({ app }) => {
  await app.setViewportSize({ width: 420, height: 660 });
  const surface = app.getByTestId("app-ready");
  await app.getByTestId("top-nav-menu").click();
  await app.getByTestId("nav-settings").click();
  await choose(app, "settings-accent", "緑");
  await expect(surface).toHaveAttribute("data-accent", "green");
  await expect(surface).toHaveClass(/v-theme--green-light/);
  await expect(app.getByTestId("settings-save")).toBeDisabled();
  await app.emulateMedia({ colorScheme: "dark" });
  await expect(surface).toHaveClass(/v-theme--green-dark/);
  await app.getByTestId("mode-expert").click();
  const nav = app.getByTestId("nav-toggle");
  await nav.click();
  await expect(nav).toHaveAttribute("aria-expanded", "true");
  await nav.press("Escape");
  await expect(nav).toBeFocused();
  await expect(nav).toHaveAttribute("aria-expanded", "false");
  await expect(app.getByTestId("open-legacy")).toBeInViewport();
  await expect(app.getByTestId("pane-switch-queue")).toBeInViewport();
});
