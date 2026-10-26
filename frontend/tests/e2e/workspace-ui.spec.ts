import {expect, test} from "@playwright/test";

test("Login layout, password visibility and keyboard navigation", async ({page}, info) => {
  await page.goto("/login");
  await expect(page.getByRole("heading", {name: "Đăng nhập", exact: true})).toBeVisible();
  const password = page.getByLabel("Mật khẩu", {exact: true});
  await password.fill("preview-only");
  await expect(password).toHaveAttribute("type", "password");
  await page.getByRole("button", {name: "Hiện ký tự", exact: true}).click();
  await expect(password).toHaveAttribute("type", "text");
  await page.getByRole("button", {name: "Ẩn ký tự", exact: true}).press("Space");
  await expect(password).toHaveAttribute("type", "password");
  await password.fill("");
  await page.screenshot({path: info.outputPath("login-desktop.png"), fullPage: true});
  await page.setViewportSize({width: 390, height: 844});
  await expect(page.getByRole("button", {name: "Đăng nhập", exact: true})).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({path: info.outputPath("login-mobile.png"), fullPage: true});
});

test("Viewer workspace navigation and logout on mobile", async ({page}, info) => {
  const password = process.env.E2E_DEMO_PASSWORD;
  if (!password) throw new Error("Use the isolated test runner");
  await page.goto("/login");
  await page.getByLabel("Tên đăng nhập").fill("viewer_all");
  await page.getByLabel("Mật khẩu", {exact: true}).fill(password);
  await page.getByRole("button", {name: "Đăng nhập", exact: true}).click();
  await expect(page).toHaveURL(/\/dashboards$/);
  await expect(page.getByRole("heading", {name: "Thư viện dashboard"})).toBeVisible();
  await expect(page.getByRole("link", {name: "AI Builder", exact: true})).toHaveCount(0);
  await page.screenshot({path: info.outputPath("library-desktop.png"), fullPage: true});
  await page.setViewportSize({width: 390, height: 844});
  await expect(page.getByRole("navigation", {name: "Điều hướng di động"})).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({path: info.outputPath("library-mobile.png"), fullPage: true});
  await page.getByRole("button", {name: "Đăng xuất", exact: true}).click();
  await expect(page).toHaveURL(/\/login$/);
});
