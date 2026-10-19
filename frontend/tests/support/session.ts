import {expect, Page} from "@playwright/test";

export async function login(page: Page, account: string) {
  const password = process.env[`E2E_${account.toUpperCase()}_PASSWORD`] ?? process.env.E2E_DEMO_PASSWORD;
  if (!password) throw new Error("E2E_DEMO_PASSWORD is required");
  await page.goto("/login");
  await page.getByLabel("Tên đăng nhập").fill(account);
  await page.getByLabel("Mật khẩu").fill(password);
  await page.getByRole("button", {name: "Đăng nhập", exact: true}).click();
  await expect(page).toHaveURL(/\/dashboards$/);
}

export async function openBuilder(page: Page) {
  await page.goto("/builder");
  await expect(page).toHaveURL(/\/builder\?conversation=/);
  await expect(page.getByLabel("Nội dung yêu cầu")).toBeEnabled();
  return new URL(page.url()).searchParams.get("conversation")!;
}

export async function send(page: Page, message: string) {
  await page.getByLabel("Nội dung yêu cầu").fill(message);
  const response = page.waitForResponse(response => response.url().endsWith("/messages") && response.request().method() === "POST");
  await page.getByRole("button", {name: "Gửi yêu cầu", exact: true}).click();
  const result = await response;
  expect(result.status()).toBe(200);
  const body = await result.json();
  await expect(page.getByLabel("Nội dung yêu cầu")).toHaveValue("");
  await expect(page.getByLabel("Nội dung yêu cầu")).toBeEnabled();
  return body;
}
