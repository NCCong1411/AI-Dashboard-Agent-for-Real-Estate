import {expect, test} from "@playwright/test";
import {login, openBuilder, send} from "../support/session";

test("Five edits through approval and independent Viewer filtering", async ({page, browser}, info) => {
  await login(page, "builder_all");
  await openBuilder(page);
  const initial = await send(page, "Tổng quan giao dịch năm 2025");
  await send(page, "Lọc California");
  const changed = await send(page, "Đổi biểu đồ theo loại bất động sản thành cột ngang");
  expect(changed.reused_query_ids).toHaveLength(4);
  await send(page, "Thêm số bất động sản có giao dịch");
  await send(page, "Bỏ biểu đồ theo bang");
  const final = await send(page, "Đổi khoảng thời gian năm 2024");
  expect(final.draft_revision).toBe(6);
  for (const widget of initial.draft.widgets.filter((item: {id: string; dimension?: string}) => item.dimension !== "state" && item.dimension !== "property_type")) {
    expect(final.draft.widgets.find((item: {id: string}) => item.id === widget.id)).toEqual(widget);
  }
  await page.getByRole("button", {name: "Duyệt và phát hành", exact: true}).click();
  const link = page.getByRole("link", {name: "Mở dashboard phiên bản 1 →", exact: true});
  await expect(link).toBeVisible();
  const path = await link.getAttribute("href");
  const viewer = await browser.newContext();
  try {
    const other = await viewer.newPage();
    await login(other, "viewer_ca");
    await other.goto(path!);
    const expectedCount = final.results.totals.rows[0].transaction_count;
    await expect(other.getByTestId("widget-card-transaction_count").locator(".kpiValue")).toHaveText(expectedCount.toLocaleString("vi-VN"));
    await expect(other.getByText(/Phạm vi: CA/)).toBeVisible();
    await expect(other.getByText("Nguồn KPI và SQL", {exact: true})).toHaveCount(0);
    await other.getByLabel("Bang", {exact: true}).fill("TX");
    await other.getByRole("button", {name: "Áp dụng bộ lọc", exact: true}).click();
    await expect(other.getByText("Không có dữ liệu trong kỳ và phạm vi đã chọn.", {exact: true})).toHaveCount(final.draft.widgets.length);
    await other.screenshot({path: info.outputPath("viewer-scope-intersection.png"), fullPage: true});
  } finally {await viewer.close();}
});

test("Offer-rate chart and exact data table use percent units", async ({page}, info) => {
  await login(page, "builder_all");
  await openBuilder(page);
  await send(page, "Tỷ lệ đề nghị được chấp nhận theo năm");
  await expect(page.locator("canvas")).toHaveCount(1);
  await expect(page.getByText("tỷ lệ (%)", {exact: true})).toBeVisible();
  await page.getByText("Xem bảng dữ liệu", {exact: true}).click();
  const row = page.getByRole("row").filter({has: page.getByRole("cell", {name: "2025-01-01", exact: true})});
  await expect(row).toContainText("22,22%");
  await page.screenshot({path: info.outputPath("offer-rate-chart.png"), fullPage: true});
});
