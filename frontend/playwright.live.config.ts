import {defineConfig} from "@playwright/test";

const baseURL = process.env.PLAYWRIGHT_BASE_URL;
if (!baseURL) throw new Error("PLAYWRIGHT_BASE_URL is required for the isolated live suite");
if (!process.env.E2E_DEMO_PASSWORD && !["BUILDER_ALL", "VIEWER_CA", "BUILDER_TX"].every(
  account => process.env[`E2E_${account}_PASSWORD`])) {
  throw new Error("Live suite requires demo credentials in process environment");
}

export default defineConfig({
  testDir: "./tests/live",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  repeatEach: 1,
  maxFailures: 1,
  timeout: 360000,
  expect: {timeout: 30000},
  outputDir: "./test-results-live",
  reporter: [["list"], ["html", {outputFolder: "./playwright-report-live", open: "never"}]],
  use: {
    baseURL,
    browserName: "chromium",
    viewport: {width: 1440, height: 1000},
    screenshot: "only-on-failure",
  },
});
