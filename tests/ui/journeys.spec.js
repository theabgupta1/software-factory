// User journeys through the Factory Console, against the demo factory (real engine state, scripted agents).
import { test, expect } from "@playwright/test";
import { JOB, VIEWS, apiPost, open, watchErrors } from "./helpers.js";

test.describe("journeys", () => {
  let errors;
  test.beforeEach(async ({ page, context }) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    errors = watchErrors(page);
  });
  test.afterEach(() => expect(errors, "no console errors or uncaught exceptions").toEqual([]));

  test("every view renders real state without errors", async ({ page }) => {
    for (const [, path] of VIEWS) {
      await open(page, path);
      await expect(page.locator(".error-box")).toHaveCount(0);
    }
  });

  test("needs-you queue → copy the approve command → open the job", async ({ page }) => {
    await open(page, "/#/");
    await expect(page.getByRole("heading", { name: /decisions? needs? you/ })).toBeVisible();
    const queue = page.locator(".attention-card");
    await expect(queue).toHaveCount(2);
    const escalated = queue.filter({ hasText: "Escalated by the approver" });
    await expect(escalated).toContainText("Show estimated reading time");
    await escalated.getByRole("button", { name: /^Copy command: / }).click();
    await expect(page.getByRole("status").filter({ hasText: "Copied to clipboard" })).toBeVisible();
    expect(await page.evaluate(() => navigator.clipboard.readText())).toMatch(/^\/factory approve 006/);
    await escalated.getByRole("link", { name: "Open" }).click();
    await expect(page).toHaveURL(new RegExp(`#/jobs/${JOB.escalated}$`));
    await expect(page.getByRole("heading", { level: 2, name: "The approver escalated this job" })).toBeVisible();
    await page.getByRole("link", { name: "decision.md" }).first().click();
    await expect(page.locator(".split-main")).toContainText("ESCALATE");
  });

  test("composer builds the exact /factory command and queues backlog items", async ({ page }) => {
    const task = `Add CSV export to the orders page ${Date.now()}`;
    await open(page, "/#/");
    await page.getByLabel("Outcome you want").fill(task);
    await page.getByLabel("Workflow").selectOption("fix");
    await expect(page.locator("#new-task .copy-field")).toContainText(`/factory fix ${task}`);
    await page.getByLabel("Workflow").selectOption("quick");
    await expect(page.getByLabel("Specialist")).toBeVisible();
    await page.getByLabel("Specialist").selectOption("frontend-engineer");
    await expect(page.locator("#new-task .copy-field")).toContainText("/factory quick");
    await expect(page.locator("#new-task .copy-field")).toContainText("frontend-engineer");
    // Survives live re-renders (the state poll runs every 2.5 s).
    await page.waitForTimeout(3000);
    await expect(page.getByLabel("Outcome you want")).toHaveValue(task);
    await page.getByRole("button", { name: "Add to backlog" }).click();
    await expect(page.getByRole("status").filter({ hasText: "Added to the backlog" })).toBeVisible();
    await expect(page.locator(".backlog-item").filter({ hasText: task })).toBeVisible();
    await expect(page.getByLabel("Outcome you want")).toHaveValue("");
  });

  test("empty composer refuses to queue", async ({ page }) => {
    await open(page, "/#/");
    await page.getByLabel("Outcome you want").fill("");
    await page.getByRole("button", { name: "Add to backlog" }).click();
    await expect(page.getByRole("status").filter({ hasText: "Describe the task first" })).toBeVisible();
    await expect(page.getByLabel("Outcome you want")).toBeFocused();
  });

  test("crew → open a running agent → send it an instruction", async ({ page }) => {
    await open(page, `/#/jobs/${JOB.running}/crew`);
    const cards = page.locator(".agent-card");
    await expect(cards.first()).toBeVisible();
    const security = page.getByRole("button", { name: "Security Engineer, Review · round 2 · security, working" });
    await expect(security.locator("svg[role='img']")).toBeVisible(); // the agent's mod avatar
    await security.click();
    const drawer = page.getByRole("dialog");
    await expect(drawer).toBeVisible();
    await expect(page).toHaveURL(/step=review\.r2%2Fsecurity/);
    const box = drawer.getByPlaceholder(/^Message Security/);
    await expect(box).toBeFocused();
    const note = `Also check the X-Forwarded-For spoofing case (${Date.now()})`;
    await box.fill(note);
    await box.press("Control+Enter");
    await expect(page.getByRole("status").filter({ hasText: "reaches the agent at its next tool call" })).toBeVisible();
    const bubble = drawer.locator(".bubble.user").filter({ hasText: note });
    await expect(bubble).toBeVisible();
    await expect(bubble).toContainText("Queued");
    await page.keyboard.press("Escape");
    await expect(drawer).toBeHidden();
    await expect(security).toBeFocused();
  });

  test("message the orchestrator", async ({ page }) => {
    await open(page, `/#/jobs/${JOB.fix}/crew`);
    await page.getByRole("button", { name: /Message the orchestrator/ }).click();
    const drawer = page.getByRole("dialog");
    const note = `Pause after the diagnosis please (${Date.now()})`;
    await drawer.getByLabel("Message the orchestrator").fill(note);
    await drawer.getByRole("button", { name: "Send" }).click();
    await expect(drawer.locator(".bubble.user").filter({ hasText: note })).toBeVisible();
    await expect(drawer.getByLabel("Message the orchestrator")).toHaveValue("");
    await page.keyboard.press("Escape");
    await expect(page.getByRole("button", { name: /Message the orchestrator/ }).locator(".count")).toBeVisible();
  });

  test("job tabs: keyboard (roving tabs and 1–7 shortcuts)", async ({ page }) => {
    await open(page, `/#/jobs/${JOB.running}`);
    const overview = page.getByRole("tab", { name: /Overview/ });
    await overview.focus();
    await page.keyboard.press("ArrowRight");
    await expect(page.getByRole("tab", { name: /Crew/ })).toBeFocused();
    await expect(page).toHaveURL(/\/crew$/);
    await page.locator("main").focus();
    await page.keyboard.press("5");
    await expect(page).toHaveURL(/\/artifacts$/);
    await expect(page.getByRole("tab", { name: /Artifacts/ })).toHaveAttribute("aria-selected", "true");
  });

  test("flow graph: nodes are keyboard operable", async ({ page }) => {
    await open(page, `/#/jobs/${JOB.running}/flow`);
    const nodes = page.locator(".flow-node");
    expect(await nodes.count()).toBeGreaterThan(5);
    await nodes.first().focus();
    await page.keyboard.press("ArrowRight");
    await expect(nodes.nth(1)).toBeFocused();
    const running = page.getByRole("button", { name: "Security Engineer, Review · round 2 · security: running" });
    await running.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("dialog")).toBeVisible();
  });

  test("artifacts: plan table, verdict banner and a deep link", async ({ page }) => {
    await open(page, `/#/jobs/${JOB.running}/artifacts?file=plan.json`);
    await expect(page.locator(".split-main table")).toContainText("T1");
    await page.getByRole("button", { name: /review-security\.md/ }).first().click();
    await expect(page.locator(".split-main")).toContainText(/CHANGES|PASS/);
    await expect(page).toHaveURL(/file=round-1%2Freview-security\.md|file=round-1\/review-security\.md/);
  });

  test("changes: commits and a real diff", async ({ page }) => {
    await open(page, `/#/jobs/${JOB.merged}/changes`);
    await expect(page.getByRole("heading", { name: "Commits" })).toBeVisible();
    await expect(page.locator(".diff-file").first()).toBeVisible();
    await expect(page.locator(".diff-file .add").first()).toBeVisible();
  });

  test("quality: review verdicts per round and open findings", async ({ page }) => {
    await open(page, `/#/jobs/${JOB.running}/quality`);
    await expect(page.getByRole("heading", { name: "Review verdicts" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Open findings" })).toBeVisible();
  });

  test("catalog: search, open a contract, Escape returns focus", async ({ page }) => {
    await open(page, "/#/catalog");
    await expect(page.locator(".catalog-card")).toHaveCount(25);
    await page.getByLabel("Search agents").fill("spark");
    await expect(page.locator(".catalog-card")).toHaveCount(1);
    const card = page.locator(".catalog-card").first();
    await expect(card).toContainText("Data Engineer");
    await card.click();
    const drawer = page.getByRole("dialog", { name: "Data Engineer contract" });
    await expect(drawer).toBeVisible();
    await expect(page).toHaveURL(/#\/catalog\/data-engineer$/);
    await expect(drawer).toContainText("Output contract");
    await page.keyboard.press("Escape");
    await expect(drawer).toBeHidden();
    await expect(page).toHaveURL(/#\/catalog$/);
    await expect(page.locator('[data-focus-key="catalog:data-engineer"]')).toBeFocused();
    await page.getByRole("button", { name: "Workflows", exact: true }).click();
    await expect(page.getByRole("heading", { name: /Feature/ }).first()).toBeVisible();
  });

  test("settings: guard rules and environment checks", async ({ page }) => {
    await open(page, "/#/settings");
    await expect(page.getByRole("heading", { name: "Safety guard" })).toBeVisible();
    await expect(page.locator("main")).toContainText("no-push");
    await expect(page.getByRole("heading", { name: "Environment checks" })).toBeVisible();
  });

  test("global shortcuts: g a, g s, g h, ? and t", async ({ page }) => {
    await open(page, "/#/");
    await page.locator("main").focus();
    await page.keyboard.press("g");
    await page.keyboard.press("a");
    await expect(page).toHaveURL(/#\/catalog$/);
    await page.locator("main").focus();
    await page.keyboard.press("g");
    await page.keyboard.press("s");
    await expect(page).toHaveURL(/#\/settings$/);
    await page.locator("main").focus();
    await page.keyboard.press("?");
    await expect(page.getByRole("dialog", { name: "Keyboard shortcuts" })).toBeVisible();
    await page.keyboard.press("Escape");
    const before = await page.evaluate(() => document.documentElement.dataset.theme || "");
    await page.locator("main").focus();
    await page.keyboard.press("t");
    await expect.poll(() => page.evaluate(() => document.documentElement.dataset.theme)).not.toBe(before);
  });

  test("legacy v1 jobs are read-only and badged", async ({ page }) => {
    await open(page, `/#/jobs/${JOB.legacy}`);
    await expect(page.locator("main")).toContainText("v1 · read-only");
    await expect(page.getByRole("heading", { name: "A job from the original factory" })).toBeVisible();
    await page.getByRole("tab", { name: /Crew/ }).click();
    await expect(page.getByRole("button", { name: /Message the orchestrator/ })).toHaveCount(0);
  });

  test("live updates: a message posted elsewhere appears without reload", async ({ page }) => {
    await open(page, "/#/");
    const row = page.locator(".job-row").filter({ hasText: "Checkout returns 500" });
    await expect(row).toBeVisible();
    const res = await apiPost(page, `/api/jobs/${JOB.fix}/messages`, { to: { agent: "builder" }, text: "Use the existing coupon fixture" });
    expect(res.status()).toBe(201);
    await expect(row.locator(".msg-badge")).toContainText("queued", { timeout: 8000 });
  });
});
