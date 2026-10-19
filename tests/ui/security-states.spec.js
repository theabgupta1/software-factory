// Untrusted text is rendered as text; error, offline and not-found states are honest and recoverable.
import { test, expect } from "@playwright/test";
import { JOB, apiPost, open } from "./helpers.js";

const PAYLOAD = `<img src=x onerror="window.__xss=1"><script>window.__xss=2</script>[click](javascript:window.__xss=3)`;

test.describe("untrusted content", () => {
  test("messages and backlog items with HTML are shown literally, never executed", async ({ page }) => {
    await open(page, "/#/");
    expect((await apiPost(page, "/api/backlog", { text: PAYLOAD })).status()).toBe(201);
    expect((await apiPost(page, `/api/jobs/${JOB.running}/messages`, { to: { role: "orchestrator" }, text: PAYLOAD })).status()).toBe(201);

    await expect(page.locator(".backlog-item").filter({ hasText: "<img src=x" })).toBeVisible({ timeout: 8000 });
    await open(page, `/#/jobs/${JOB.running}/crew`);
    await page.getByRole("button", { name: /Message the orchestrator/ }).click();
    await expect(page.getByRole("dialog")).toContainText('<img src=x onerror="window.__xss=1">');
    expect(await page.evaluate(() => window.__xss)).toBeUndefined();
    await expect(page.locator('img[src="x"]')).toHaveCount(0);
    await expect(page.locator('a[href^="javascript:"]')).toHaveCount(0);
  });

  test("the CSP blocks inline script even if markup were injected", async ({ page }) => {
    await open(page, "/#/");
    const ran = await page.evaluate(() => new Promise((resolve) => {
      window.__inline = false;
      const s = document.createElement("script");
      s.textContent = "window.__inline = true";
      document.body.appendChild(s);
      setTimeout(() => resolve(window.__inline), 100);
    }));
    expect(ran).toBe(false);
  });

  test("write API refuses requests without the page token", async ({ page }) => {
    await open(page, "/#/");
    const base = new URL(page.url()).origin;
    const res = await page.request.post(`${base}/api/backlog`, { data: { text: "sneaky" }, headers: { Origin: base } });
    expect(res.status()).toBe(403);
  });
});

test.describe("states", () => {
  test("server unreachable → error with retry, then recovers", async ({ page }) => {
    await page.route("**/api/state", (route) => route.abort("connectionrefused"));
    await page.goto("/#/");
    const alert = page.getByRole("alert");
    await expect(alert).toContainText("Something went wrong");
    await expect(page.locator(".live")).toContainText("Disconnected");
    await page.unroute("**/api/state");
    await alert.getByRole("button", { name: "Try again" }).click();
    await expect(page.locator("main h1")).toBeVisible();
    await expect(page.locator(".live")).toContainText("Live");
  });

  test("unknown job → not found with a way back", async ({ page }) => {
    await page.goto("/#/jobs/999-does-not-exist");
    await expect(page.getByText("Job not found")).toBeVisible();
    await page.getByRole("link", { name: "Back to the Command Center" }).click();
    await expect(page).toHaveURL(/#\/$/);
  });

  test("loading state is announced as busy", async ({ page }) => {
    let release;
    const gate = new Promise((r) => { release = r; });
    await page.route("**/api/state", async (route) => { await gate; await route.continue(); });
    await page.goto("/#/");
    await expect(page.locator("main [aria-busy='true']")).toBeVisible();
    release();
    await expect(page.locator("main h1")).toBeVisible();
  });

  test("a job detail that fails to load mid-session keeps the last good view", async ({ page }) => {
    await open(page, `/#/jobs/${JOB.running}`);
    await page.route(`**/api/jobs/${JOB.running}`, (route) => route.fulfill({ status: 500, body: "{}" }));
    await page.waitForTimeout(3500);
    await expect(page.locator("main h1")).toContainText("Rate limit the signup form");
  });
});
