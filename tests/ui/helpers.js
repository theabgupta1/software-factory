// Shared helpers for the console UI specs.
import { expect } from "@playwright/test";

export const JOB = {
  merged: "003-dark-mode-toggle-header",
  running: "004-rate-limit-signup-form",
  planApproval: "005-build-daily-incremental-orders",
  escalated: "006-show-estimated-reading-time",
  research: "007-which-vector-store-should",
  fix: "008-checkout-returns-500-when",
  legacy: "001-blog-reading-time",
};

export const VIEWS = [
  ["home", "/#/"],
  ["job-overview", `/#/jobs/${JOB.running}`],
  ["job-crew", `/#/jobs/${JOB.running}/crew`],
  ["job-flow", `/#/jobs/${JOB.running}/flow`],
  ["job-timeline", `/#/jobs/${JOB.running}/timeline`],
  ["job-artifacts", `/#/jobs/${JOB.running}/artifacts`],
  ["job-changes", `/#/jobs/${JOB.running}/changes`],
  ["job-quality", `/#/jobs/${JOB.running}/quality`],
  ["job-escalated", `/#/jobs/${JOB.escalated}`],
  ["job-legacy", `/#/jobs/${JOB.legacy}`],
  ["catalog", "/#/catalog"],
  ["catalog-workflows", "/#/catalog?view=workflows"],
  ["settings", "/#/settings"],
];

/** Collect console errors and uncaught exceptions for the lifetime of the page. */
export function watchErrors(page) {
  const errors = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(String(e)));
  return errors;
}

/** Navigate and wait until the view has real content (not the loading skeleton). */
export async function open(page, path) {
  await page.goto(path);
  await expect(page.locator("main [aria-busy='true']")).toHaveCount(0);
  await expect(page.locator("main h1").first()).toBeVisible();
}

export async function setTheme(page, theme) {
  await page.emulateMedia({ colorScheme: theme });
  await page.evaluate((t) => { try { localStorage.setItem("factory.theme", t); } catch { /* ignore */ } document.documentElement.dataset.theme = t; }, theme);
}

/** The console's per-run key (fixed for the test server by playwright.config.js). */
export async function token() {
  return process.env.FACTORY_CONSOLE_TOKEN;
}

export async function apiPost(page, path, body) {
  const base = new URL(page.url()).origin;
  return page.request.post(base + path, {
    data: body,
    headers: { "X-Factory-Token": await token(page), Origin: base, "Content-Type": "application/json" },
  });
}
