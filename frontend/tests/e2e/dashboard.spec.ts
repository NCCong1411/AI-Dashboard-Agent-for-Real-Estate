import {expect, test, Page} from "@playwright/test";

async function openDashboard(page: Page, account = "viewer_ca") {
  const password = process.env.E2E_DEMO_PASSWORD;
  if (!password) throw new Error("E2E_DEMO_PASSWORD must be provided by the isolated test runner");
  await page.goto("/login");
  await page.getByLabel("Tên đăng nhập").fill(account);
  await page.getByLabel("Mật khẩu").fill(password);
  await page.getByRole("button", {name: "Đăng nhập", exact: true}).click();
  await expect(page).toHaveURL(/\/dashboards$/);
  await page.getByRole("link", {name: /Tổng quan giao dịch/}).click();
  await expect(page.getByTestId("widget-transaction-count")).toContainText(account === "viewer_ca" ? "135" : "1.000");
}

test("Viewer CA opens real charts, filters, empty scope and reload", async ({page}, info) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await openDashboard(page);
  await expect(page.locator("canvas")).toHaveCount(3);
  await expect(page.getByTestId("widget-sales-value")).toContainText("107.064.313,73");
  await expect(page.getByText("Nguồn KPI và SQL", {exact: true})).toHaveCount(0);
  await expect(page.getByRole("button", {name: /duyệt|Agent/i})).toHaveCount(0);
  let release = () => {};
  const paused = new Promise<void>(resolve => {release = resolve;});
  await page.route(/\/api\/dashboards\/[^/]+\/query$/, async route => {
    await paused;
    await route.continue();
  });
  await page.getByRole("button", {name: "Tải lại dữ liệu"}).click();
  try {
    await expect(page.locator(".dashboardWidget").getByText("Đang tải dữ liệu…", {exact: true})).toHaveCount(6);
  } finally {release();}
  await expect(page.getByTestId("widget-transaction-count")).toContainText("135");
  await page.unroute(/\/api\/dashboards\/[^/]+\/query$/);
  await page.getByLabel("Từ ngày", {exact: true}).fill("2025-01-01");
  await page.getByLabel("Đến ngày (bao gồm)").fill("2025-12-31");
  await page.getByRole("button", {name: "Áp dụng bộ lọc"}).click();
  await expect(page.getByTestId("widget-transaction-count")).toContainText("15");
  await expect(page.getByTestId("widget-sales-value")).toContainText("10.321.229,42");
  await page.getByRole("button", {name: "Tải lại dữ liệu"}).click();
  await expect(page.getByTestId("widget-transaction-count")).toContainText("15");
  await page.screenshot({path: info.outputPath("dashboard-desktop.png"), fullPage: true});
  await page.setViewportSize({width: 390, height: 844});
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await expect.poll(() => page.locator("canvas").first().evaluate(element => element.getBoundingClientRect().width)).toBeLessThan(390);
  await page.screenshot({path: info.outputPath("dashboard-mobile.png"), fullPage: true});
  await page.getByLabel("Bang", {exact: true}).fill("TX");
  await page.getByRole("button", {name: "Áp dụng bộ lọc"}).click();
  await expect(page.getByText("Không có dữ liệu trong kỳ và phạm vi đã chọn.")).toHaveCount(6);
  await page.getByLabel("Bang", {exact: true}).fill("");
  await page.getByLabel("Loại bất động sản").fill("nonexistent");
  await page.getByRole("button", {name: "Áp dụng bộ lọc"}).click();
  await expect(page.getByText("Không có dữ liệu trong kỳ và phạm vi đã chọn.")).toHaveCount(6);
  expect(errors).toEqual([]);
});

test("Builder SQL, all widget types, literal HTML labels, per-widget error and expiry", async ({page}) => {
  const payload = '<img src=x onerror="window.badLabel=true"> Nhãn dài '.repeat(3);
  await page.route(/\/api\/dashboards\/[^/]+$/, async route => {
    const response = await route.fetch();
    const body = await response.json();
    if (!body.spec) return route.fulfill({response});
    body.spec.widgets[2].chart = "table";
    body.spec.widgets[2].title = payload;
    for (const [id, chart] of [["test-horizontal", "horizontal_bar"], ["test-pie", "pie"]]) {
      body.spec.widgets.push({...body.spec.widgets[4], id, chart, title: chart});
      body.spec.layout.push({widget_id: id, x: chart === "pie" ? 6 : 0, y: 6, width: 6, height: 4});
    }
    await route.fulfill({response, json: body});
  });
  await openDashboard(page, "builder_all");
  await expect(page.getByText("Nguồn KPI và SQL", {exact: true})).toBeVisible();
  await expect(page.locator(".dashboardWidget")).toHaveCount(8);
  await expect(page.locator("canvas")).toHaveCount(5);
  await expect(page.getByTestId("widget-average-value").getByRole("table")).toBeVisible();
  await expect(page.getByRole("heading", {name: payload, exact: true})).toBeVisible();
  await expect(page.locator("img")).toHaveCount(0);
  expect(await page.evaluate(() => (window as Window & {badLabel?: boolean}).badLabel)).toBeUndefined();
  await page.route(/\/api\/dashboards\/[^/]+\/query$/, async route => {
    const response = await route.fetch();
    const body = await response.json();
    body.results.by_month = {...body.results.by_month, status: "error", rows: [], warnings: ["Lỗi widget thử nghiệm"]};
    await route.fulfill({response, json: body});
  });
  await page.getByRole("button", {name: "Tải lại dữ liệu"}).click();
  await expect(page.getByTestId("widget-monthly-sales")).toContainText("Lỗi widget thử nghiệm");
  await expect(page.getByTestId("widget-transaction-count")).toContainText("1.000");
  await page.unroute(/\/api\/dashboards\/[^/]+\/query$/);
  await page.route(/\/api\/dashboards\/[^/]+\/query$/, route => route.fulfill({status: 401, json: {detail: "Not authenticated"}}));
  await page.getByRole("button", {name: "Tải lại dữ liệu"}).click();
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByTestId("widget-transaction-count")).toHaveCount(0);
});

test("Display keeps money precision beyond JavaScript safe integer", async ({page}) => {
  await page.route(/\/api\/dashboards\/[^/]+\/query$/, async route => {
    const response = await route.fetch();
    const body = await response.json();
    body.results.sales.rows[0].sales_value = "9007199254740993.17";
    await route.fulfill({response, json: body});
  });
  await openDashboard(page);
  await expect(page.getByTestId("widget-sales-value")).toContainText("9.007.199.254.740.993,17");
});
