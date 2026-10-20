// Accessibility (axe, WCAG 2.1 AA rules) in both themes, and layout at desktop, tablet and phone widths.
import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { JOB, VIEWS, open, setTheme } from "./helpers.js";

const AXE_VIEWS = VIEWS.filter(([name]) => !["job-legacy", "catalog-workflows"].includes(name));

for (const theme of ["light", "dark"]) {
  test.describe(`axe · ${theme}`, () => {
    for (const [name, path] of AXE_VIEWS) {
      test(`${name} has no serious or critical violations`, async ({ page }) => {
        await page.emulateMedia({ colorScheme: theme });
        await open(page, path);
        await setTheme(page, theme);
        const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
        const bad = result.violations.filter((v) => ["serious", "critical"].includes(v.impact));
        expect(bad.map((v) => `${v.id}: ${v.help} → ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join(" | ")}`)).toEqual([]);
      });
    }

    test("open agent drawer has no serious or critical violations", async ({ page }) => {
      await page.emulateMedia({ colorScheme: theme });
      await open(page, `/#/jobs/${JOB.running}/crew?step=${encodeURIComponent("review.r2/security")}`);
      await setTheme(page, theme);
      await expect(page.getByRole("dialog")).toBeVisible();
      const result = await new AxeBuilder({ page }).include(".drawer").withTags(["wcag2a", "wcag2aa"]).analyze();
      expect(result.violations.filter((v) => ["serious", "critical"].includes(v.impact)).map((v) => v.id)).toEqual([]);
    });
  });
}

test.describe("structure", () => {
  test("skip link, landmarks and one h1 per view", async ({ page }) => {
    await open(page, "/#/");
    await page.keyboard.press("Tab");
    const skip = page.getByRole("link", { name: "Skip to content" });
    await expect(skip).toBeFocused();
    await expect(page.getByRole("navigation", { name: "Primary" })).toHaveCount(1);
    await expect(page.getByRole("main")).toHaveCount(1);
    for (const [, path] of VIEWS) {
      await open(page, path);
      await expect(page.locator("main h1")).toHaveCount(1);
    }
  });

  test("reduced motion turns off the pulse animations", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await open(page, `/#/jobs/${JOB.running}/crew`);
    // Animations may still be declared, but none may run longer than a frame or repeat.
    const moving = await page.evaluate(() => [...document.querySelectorAll("*")].filter((n) => {
      const cs = getComputedStyle(n);
      if (!cs.animationName || cs.animationName === "none") return false;
      return parseFloat(cs.animationDuration) > 0.01 || cs.animationIterationCount === "infinite";
    }).map((n) => `${n.tagName}.${n.getAttribute("class")}`));
    expect(moving).toEqual([]);
  });
});

test.describe("responsive @responsive", () => {
  for (const [name, path] of VIEWS) {
    test(`${name} has no page-level horizontal scroll`, async ({ page }) => {
      await open(page, path);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow).toBeLessThanOrEqual(0);
    });
  }

  test("navigation is reachable at every width", async ({ page }) => {
    await open(page, "/#/");
    const nav = page.getByRole("navigation", { name: "Primary" });
    const menu = page.getByRole("button", { name: "Open navigation" });
    if (await menu.isVisible()) {
      await menu.click();
    }
    await nav.getByRole("link", { name: "Agents & workflows" }).click();
    await expect(page).toHaveURL(/#\/catalog$/);
  });

  test("agent drawer fits the viewport", async ({ page }) => {
    await open(page, `/#/jobs/${JOB.running}/crew?step=${encodeURIComponent("review.r2/security")}`);
    const drawer = page.getByRole("dialog");
    await expect(drawer).toBeVisible();
    const vw = page.viewportSize().width;
    // Wait for the slide-in transition to settle, then the whole panel must be on screen.
    await expect.poll(async () => { const b = await drawer.boundingBox(); return Math.round(b.x + b.width); }).toBeLessThanOrEqual(vw + 1);
    expect((await drawer.boundingBox()).x).toBeGreaterThanOrEqual(0);
    await expect(drawer.getByRole("button", { name: "Send" })).toBeInViewport();
  });
});
