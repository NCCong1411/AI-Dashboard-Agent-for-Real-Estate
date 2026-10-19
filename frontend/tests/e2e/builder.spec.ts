import {expect, test, Page} from "@playwright/test";

async function login(page: Page, account: string) {
  if (!process.env.E2E_DEMO_PASSWORD) throw new Error("E2E_DEMO_PASSWORD is required");
  await page.goto("/login");
  await page.getByLabel("Tên đăng nhập").fill(account);
  await page.getByLabel("Mật khẩu").fill(process.env.E2E_DEMO_PASSWORD);
  await page.getByRole("button", {name: "Đăng nhập", exact: true}).click();
  await expect(page).toHaveURL(/\/dashboards$/);
}

test("Builder TX creates a real scoped draft, clarifies, refuses, and restores", async ({page}, info) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await login(page, "builder_tx");
  await page.getByRole("link", {name: "Tạo dashboard bằng hội thoại →"}).click();
  await expect(page).toHaveURL(/\/builder\?conversation=/);
  await expect(page.getByText(/Chế độ thử nghiệm:/)).toBeVisible();
  await page.getByLabel("Nội dung yêu cầu").fill("Tạo tổng quan giao dịch toàn bộ thời gian");
  await page.getByRole("button", {name: "Gửi yêu cầu", exact: true}).click();
  await expect(page.getByTestId("widget-card-transaction_count")).toContainText("132");
  await expect(page.getByTestId("widget-card-sales_value")).toContainText("128.635.636,80");
  await expect(page.getByText("Bản nháp 1", {exact: true})).toBeVisible();
  await expect(page.locator("canvas")).toHaveCount(3);
  await page.getByText("Nguồn KPI và SQL của bản nháp", {exact: true}).click();
  await expect(page.locator("pre").first()).toContainText("analytics.sales");
  await page.screenshot({path: info.outputPath("builder-preview-desktop.png"), fullPage: true});
  const url = page.url();
  await page.reload();
  await expect(page.getByTestId("widget-card-transaction_count")).toContainText("132");
  await expect(page).toHaveURL(url);
  await page.setViewportSize({width: 390, height: 844});
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({path: info.outputPath("builder-preview-mobile.png"), fullPage: true});
  await page.getByLabel("Nội dung yêu cầu").fill("Doanh thu năm nay");
  await page.getByRole("button", {name: "Gửi yêu cầu", exact: true}).click();
  await expect(page.getByText("Bạn muốn đo giá trị giao dịch hay một chỉ tiêu nào khác?", {exact: true}).first()).toBeVisible();
  await page.getByLabel("Nội dung yêu cầu").fill("DROP TABLE transactions");
  await page.getByRole("button", {name: "Gửi yêu cầu", exact: true}).click();
  await expect(page.getByText("Yêu cầu này không nằm trong phạm vi KPI được hỗ trợ.", {exact: true}).first()).toBeVisible();
  expect(errors).toEqual([]);
});

test("Viewer is redirected away from Builder and cannot read a foreign conversation", async ({page, browser}) => {
  await login(page, "builder_all");
  await page.goto("/builder");
  await expect(page).toHaveURL(/\/builder\?conversation=/);
  const conversationUrl = page.url();
  const viewer = await browser.newContext();
  const viewerPage = await viewer.newPage();
  await login(viewerPage, "viewer_ca");
  await expect(viewerPage.getByRole("link", {name: "Tạo dashboard bằng hội thoại →"})).toHaveCount(0);
  await viewerPage.goto(conversationUrl);
  await expect(viewerPage).toHaveURL(/\/dashboards$/);
  await expect(viewerPage.getByLabel("Nội dung yêu cầu")).toHaveCount(0);
  await viewer.close();
});

test("Five edits preserve widgets and reuse results on presentation changes", async ({page}, info) => {
  await login(page, "builder_all");
  await page.goto("/builder");
  await expect(page).toHaveURL(/\/builder\?conversation=/);
  async function send(message: string) {
    await page.getByLabel("Nội dung yêu cầu").fill(message);
    const response = page.waitForResponse(response => response.url().endsWith("/messages") && response.request().method() === "POST");
    await page.getByRole("button", {name: "Gửi yêu cầu", exact: true}).click();
    const body = await (await response).json();
    await expect(page.getByRole("button", {name: "Gửi yêu cầu", exact: true})).toBeDisabled();
    await expect(page.getByText(`Bản nháp ${body.draft_revision}`, {exact: true})).toBeVisible();
    return body;
  }
  await send("Tổng quan giao dịch năm 2025");
  await expect(page.getByTestId("widget-card-transaction_count")).toContainText("97");
  await send("Lọc California");
  await expect(page.getByTestId("widget-card-transaction_count")).toContainText("15");
  const changed = await send("Đổi biểu đồ theo loại bất động sản thành cột ngang");
  expect(changed.reused_query_ids).toHaveLength(4);
  await expect(page.getByRole("region", {name: "Thay đổi của bản nháp"})).toContainText("Giá trị giao dịch theo loại BĐS");
  await send("Thêm số bất động sản có giao dịch");
  await expect(page.getByTestId("widget-widget-sold_property_count")).toContainText("7");
  await send("Bỏ biểu đồ theo bang");
  await expect(page.getByTestId("widget-chart-state")).toHaveCount(0);
  const final = await send("Đổi khoảng thời gian năm 2024");
  expect(final.draft_revision).toBe(6);
  await expect(page.getByText("Bản nháp 6", {exact: true})).toBeVisible();
  await page.reload();
  await expect(page.getByText("Bản nháp 6", {exact: true})).toBeVisible();
  await expect(page.getByTestId("widget-widget-sold_property_count")).toBeVisible();
  await page.screenshot({path: info.outputPath("builder-five-edits.png"), fullPage: true});
});

test("Approval survives a lost response and publishes scoped versions to Viewer", async ({page, browser}, info) => {
  await login(page, "builder_all");
  await page.goto("/builder");
  await expect(page).toHaveURL(/\/builder\?conversation=/);
  async function send(message: string) {
    await page.getByLabel("Nội dung yêu cầu").fill(message);
    await page.getByRole("button", {name: "Gửi yêu cầu", exact: true}).click();
    await expect(page.getByLabel("Nội dung yêu cầu")).toHaveValue("");
    await expect(page.getByLabel("Nội dung yêu cầu")).toBeEnabled();
  }
  await send("Tổng quan giao dịch năm 2025");
  await send("Đổi tên dashboard thành Nghiệm thu phát hành");
  await expect(page.getByRole("region", {name: "Duyệt bản nháp"})).toBeVisible();
  await page.screenshot({path: info.outputPath("approval-preview.png"), fullPage: true});
  const viewer = await browser.newContext();
  try {
    const viewerPage = await viewer.newPage();
    await login(viewerPage, "viewer_ca");
    await expect(viewerPage.getByRole("heading", {name: "Nghiệm thu phát hành", exact: true})).toHaveCount(0);
    // Commit through the real backend, then drop only its first HTTP response.
    let committedDashboard = "";
    await page.route(/\/api\/conversations\/[^/]+\/approval$/, async route => {
      const committed = await route.fetch();
      expect(committed.status()).toBe(200);
      committedDashboard = (await committed.json()).dashboard_id;
      await route.abort("failed");
    });
    await page.getByRole("button", {name: "Duyệt và phát hành", exact: true}).click();
    await expect(page.locator("main").getByRole("alert")).toContainText("Chưa nhận được kết quả duyệt");
    await page.unroute(/\/api\/conversations\/[^/]+\/approval$/);
    await page.getByRole("button", {name: "Duyệt và phát hành", exact: true}).click();
    await expect(page.getByRole("heading", {name: "Đã phát hành dashboard", exact: true})).toBeVisible();
    const published = page.getByRole("link", {name: "Mở dashboard phiên bản 1 →", exact: true});
    await expect(published).toHaveAttribute("href", `/dashboards/${committedDashboard}`);
    await page.reload();
    await expect(page.getByRole("link", {name: "Mở dashboard phiên bản 1 →", exact: true})).toBeVisible();
    await viewerPage.goto(`/dashboards/${committedDashboard}`);
    await expect(viewerPage.getByTestId("widget-card-transaction_count")).toContainText("15");
    await expect(viewerPage.getByText("Nguồn KPI và SQL", {exact: true})).toHaveCount(0);
    await expect(viewerPage.getByText("Phiên bản 1 · Đã duyệt", {exact: true})).toBeVisible();
    const competing = await page.context().newPage();
    await competing.goto(`/builder?dashboard=${committedDashboard}`);
    await expect(competing).toHaveURL(/\/builder\?conversation=/);
    await competing.getByLabel("Nội dung yêu cầu").fill("Lọc Texas");
    await competing.getByRole("button", {name: "Gửi yêu cầu", exact: true}).click();
    await expect(competing.getByRole("button", {name: "Duyệt và phát hành", exact: true})).toBeEnabled();
    await send("Lọc California");
    await expect(page.getByTestId("widget-card-transaction_count")).toContainText("15");
    await viewerPage.reload();
    await expect(viewerPage.getByText("Phiên bản 1 · Đã duyệt", {exact: true})).toBeVisible();
    await page.getByRole("button", {name: "Duyệt và phát hành", exact: true}).click();
    await expect(page.getByRole("link", {name: "Mở dashboard phiên bản 2 →", exact: true})).toBeVisible();
    await viewerPage.getByRole("button", {name: "Tải lại dữ liệu", exact: true}).click();
    await expect(viewerPage.getByText("Phiên bản 2 · Đã duyệt", {exact: true})).toBeVisible();
    await expect(viewerPage.getByTestId("widget-card-transaction_count")).toContainText("15");
    await competing.getByRole("button", {name: "Duyệt và phát hành", exact: true}).click();
    await expect(competing.locator("main").getByRole("alert")).toContainText("Dashboard đã có phiên bản mới");
    await expect(competing.getByRole("link", {name: "Mở dashboard đã duyệt hiện tại →", exact: true})).toHaveAttribute("href", `/dashboards/${committedDashboard}`);
    await competing.close();
    await page.reload();
    await expect(page.getByRole("link", {name: "Mở dashboard phiên bản 2 →", exact: true})).toBeVisible();
    await expect(page.getByRole("button", {name: "Duyệt và phát hành", exact: true})).toHaveCount(0);
    await page.screenshot({path: info.outputPath("approval-published.png"), fullPage: true});
  } finally {await viewer.close();}
});

test("Reject does not publish and allows further draft editing", async ({page}) => {
  await login(page, "builder_tx");
  await page.goto("/builder");
  await expect(page).toHaveURL(/\/builder\?conversation=/);
  await page.getByLabel("Nội dung yêu cầu").fill("Tổng quan giao dịch năm 2025");
  await page.getByRole("button", {name: "Gửi yêu cầu", exact: true}).click();
  await expect(page.getByRole("button", {name: "Từ chối bản nháp", exact: true})).toBeEnabled();
  await page.getByRole("button", {name: "Từ chối bản nháp", exact: true}).click();
  await expect(page.getByRole("heading", {name: "Đã từ chối bản nháp", exact: true})).toBeVisible();
  await expect(page.getByRole("link", {name: /Mở dashboard phiên bản/})).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole("heading", {name: "Đã từ chối bản nháp", exact: true})).toBeVisible();
  await page.getByLabel("Nội dung yêu cầu").fill("Đổi khoảng thời gian năm 2024");
  await page.getByRole("button", {name: "Gửi yêu cầu", exact: true}).click();
  await expect(page.getByText("Bản nháp 2", {exact: true})).toBeVisible();
  await expect(page.getByRole("button", {name: "Duyệt và phát hành", exact: true})).toBeEnabled();
});

test("Mixed facts preserve sales totals, exact ratio formatting and Viewer scope", async ({page, browser}, info) => {
  await login(page, "builder_all");
  await page.goto("/builder");
  await expect(page).toHaveURL(/\/builder\?conversation=/);
  await page.getByLabel("Nội dung yêu cầu").fill("Giá trị giao dịch, số đề nghị, tỷ lệ đề nghị được chấp nhận và chi phí sửa chữa năm 2025");
  const response = page.waitForResponse(response => response.url().endsWith("/messages") && response.request().method() === "POST");
  await page.getByRole("button", {name: "Gửi yêu cầu", exact: true}).click();
  const draft = await (await response).json();
  expect(draft.draft.queries).toHaveLength(3);
  await expect(page.getByTestId("widget-card-sales_value")).toContainText("81.983.427,35");
  await expect(page.getByTestId("widget-card-offer_count")).toContainText("9");
  await expect(page.getByTestId("widget-card-accepted_offer_rate")).toContainText("22,22%");
  await expect(page.getByTestId("widget-card-repair_cost")).toContainText("367.957,16");
  await page.screenshot({path: info.outputPath("mixed-facts-preview.png"), fullPage: true});
  await page.getByRole("button", {name: "Duyệt và phát hành", exact: true}).click();
  const link = page.getByRole("link", {name: "Mở dashboard phiên bản 1 →", exact: true});
  await expect(link).toBeVisible();
  const path = await link.getAttribute("href");
  const viewer = await browser.newContext();
  try {
    const viewerPage = await viewer.newPage();
    await login(viewerPage, "viewer_ca");
    await viewerPage.goto(path!);
    await expect(viewerPage.getByTestId("widget-card-sales_value")).toContainText("10.321.229,42");
    await expect(viewerPage.getByTestId("widget-card-offer_count")).toContainText("2");
    await expect(viewerPage.getByTestId("widget-card-accepted_offer_rate")).toContainText("0,00%");
    await expect(viewerPage.getByTestId("widget-card-repair_cost")).toContainText("25.985,57");
    await viewerPage.getByLabel("Từ ngày", {exact: true}).fill("2030-01-01");
    await viewerPage.getByLabel("Đến ngày (bao gồm)").fill("2030-12-31");
    await viewerPage.getByRole("button", {name: "Áp dụng bộ lọc", exact: true}).click();
    await expect(viewerPage.getByText("Không có dữ liệu trong kỳ và phạm vi đã chọn.", {exact: true})).toHaveCount(4);
  } finally {await viewer.close();}
});
