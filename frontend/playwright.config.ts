import {defineConfig} from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 45000,
  expect: {timeout: 15000},
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:3000",
    browserName: "chromium",
    viewport: {width: 1440, height: 1000},
    screenshot: "only-on-failure",
  },
});
