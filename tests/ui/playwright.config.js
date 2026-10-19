// Factory Console UI tests: real browser against `factoryctl serve --demo` (the real engine driven by scripted
// agents in a throwaway repo). Run: npx playwright test -c tests/ui/playwright.config.js
import { defineConfig, devices } from "@playwright/test";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const PORT = Number(process.env.FACTORY_UI_PORT || 8790);
const claudeHome = mkdtempSync(join(tmpdir(), "factory-ui-claude-home-"));

export default defineConfig({
  testDir: ".",
  testMatch: /.*\.spec\.js$/,
  outputDir: join(ROOT, "test-results", "ui"),
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 30_000,
  expect: { timeout: 8_000 },
  reporter: [["list"], ["json", { outputFile: join(ROOT, "test-results", "ui.json") }]],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } },
    { name: "tablet", use: { ...devices["Desktop Chrome"], viewport: { width: 900, height: 1000 } }, grep: /@responsive/ },
    { name: "mobile", use: { ...devices["Desktop Chrome"], viewport: { width: 390, height: 844 }, hasTouch: true },
      grep: /@responsive/ },
  ],
  webServer: {
    command: `python3 plugins/factory/bin/factoryctl serve --demo --port ${PORT}`,
    cwd: ROOT,
    url: `http://127.0.0.1:${PORT}/`,
    reuseExistingServer: false,
    timeout: 60_000,
    env: { CLAUDE_CONFIG_DIR: claudeHome },
  },
});
