import {expect, test} from "@playwright/test";
import {login, openBuilder, send} from "../support/session";
import {restartBackend} from "../support/restart";

test("Actual backend restart preserves the browser draft and approval thread", async ({page}, info) => {
  await login(page, "builder_all");
  const conversation = await openBuilder(page);
  const draft = await send(page, "Tổng quan giao dịch năm 2025");
  await restartBackend();
  await page.reload();
  await expect(page.getByText("Bản nháp 1", {exact: true})).toBeVisible();
  await expect(page.getByTestId("widget-card-transaction_count")).toContainText("97");
  const current = await (await page.request.get(`/api/conversations/${conversation}`)).json();
  expect(current.draft_hash).toBe(draft.draft_hash);
  expect(current.messages).toHaveLength(2);
  expect(current.response.results).toEqual(draft.results);
  await page.getByRole("button", {name: "Duyệt và phát hành", exact: true}).click();
  await expect(page.getByRole("link", {name: "Mở dashboard phiên bản 1 →", exact: true})).toBeVisible();
  await page.screenshot({path: info.outputPath("restart-then-approved.png"), fullPage: true});
});

test("Transient message failure retains draft and allows retry", async ({page}) => {
  await login(page, "builder_all");
  await openBuilder(page);
  await send(page, "Tổng quan giao dịch năm 2025");
  await page.route(/\/api\/conversations\/[^/]+\/messages$/, route => route.fulfill({status: 503, json: {detail: "temporary test outage"}}));
  await page.getByLabel("Nội dung yêu cầu").fill("Lọc California");
  await page.getByRole("button", {name: "Gửi yêu cầu", exact: true}).click();
  await expect(page.locator("main").getByRole("alert")).toBeVisible();
  await expect(page.getByTestId("widget-card-transaction_count")).toContainText("97");
  await expect(page.getByLabel("Nội dung yêu cầu")).toHaveValue("Lọc California");
  await page.unroute(/\/api\/conversations\/[^/]+\/messages$/);
  await send(page, "Lọc California");
  await expect(page.getByTestId("widget-card-transaction_count")).toContainText("15");
});

test("Actual reader connection failure shows widget errors then recovers", async ({page}, info) => {
  await login(page, "viewer_ca");
  await page.getByRole("link", {name: /Tổng quan giao dịch/}).click();
  await expect(page.getByTestId("widget-transaction-count")).toContainText("135");
  try {
    await restartBackend("reader");
    await page.reload();
    await expect(page.locator(".dashboardWidget").getByRole("alert")).toHaveCount(6);
    await expect(page.getByRole("button", {name: "Tải lại dữ liệu", exact: true})).toBeEnabled();
    await page.screenshot({path: info.outputPath("reader-outage.png"), fullPage: true});
  } finally {await restartBackend();}
  await page.reload();
  await expect(page.getByTestId("widget-transaction-count")).toContainText("135");
});

test("Provider adapter failure preserves the last preview and permits a later retry", async ({page}) => {
  await login(page, "builder_all");
  await openBuilder(page);
  await send(page, "Tổng quan giao dịch năm 2025");
  try {
    await restartBackend("provider");
    await page.reload();
    await expect(page.getByTestId("widget-card-transaction_count")).toContainText("97");
    const failed = await send(page, "Lọc California");
    expect(failed.status).toBe("failed");
    expect(failed.error.code).toBe("adapter_error");
    expect(JSON.stringify(failed)).not.toContain("private-provider-failure-canary");
    await expect(page.getByTestId("widget-card-transaction_count")).toContainText("97");
    await expect(page.getByText("Bản nháp 1", {exact: true})).toBeVisible();
  } finally {await restartBackend();}
  await page.reload();
  await expect(page.getByLabel("Nội dung yêu cầu")).toBeEnabled();
  await send(page, "Lọc California");
  await expect(page.getByTestId("widget-card-transaction_count")).toContainText("15");
});
