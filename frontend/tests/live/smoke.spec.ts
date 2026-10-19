import {expect, test} from "@playwright/test";
import {writeFile} from "node:fs/promises";
import {login, openBuilder, send} from "../support/session";
import {restartBackend} from "../support/restart";

type Widget = {id: string; kpi: string; dimension: string | null; chart: string; query_id: string};
type Query = {id: string; plan: {filters: {field: string; op: string; value: string | string[]}[];
  date_range: {start: string | null; end_exclusive: string | null}}};
type Draft = {widgets: Widget[]; queries: Query[]};
type LiveResponse = {outcome: string; status: string; mode: string; draft_revision: number;
  draft_hash: string; base_version_number: number | null; sql: Record<string, string>;
  run_id: string; draft: Draft; results: Record<string, {status: string}>;
  changes?: {presentation_only?: boolean}; reused_query_ids: string[]};

function widget(draft: Draft, kpi: string, dimension: string | null): Widget {
  const matches = draft.widgets.filter(item => item.kpi === kpi && item.dimension === dimension);
  expect(matches, `Expected one ${kpi}/${dimension ?? "card"} widget`).toHaveLength(1);
  return matches[0];
}

function expectHealthy(response: LiveResponse): void {
  expect(response.mode).toBe("live");
  expect(response.outcome).toBe("plan");
  expect(response.status).toBe("awaiting_approval");
  expect(response.run_id).toBeTruthy();
  expect(response.draft.widgets.length).toBeGreaterThan(0);
  for (const result of Object.values(response.results)) expect(result.status).not.toBe("error");
}

function expectStateAndDates(draft: Draft, state: string, start: string, end: string): void {
  for (const query of draft.queries) {
    expect(query.plan.date_range).toEqual({start, end_exclusive: end});
    expect(query.plan.filters).toContainEqual({field: "state", op: "eq", value: state});
  }
}

test("Live planner: five edits, approval, and Viewer scope", async ({page, browser}, info) => {
  await login(page, "builder_all");
  const dashboards = await page.request.get("/api/dashboards");
  expect(dashboards.status()).toBe(200);
  const seeded = (await dashboards.json()).find((item: {title: string}) => item.title === "Tổng quan giao dịch bất động sản");
  expect(seeded?.id).toBeTruthy();
  const txRequest = {data: {filters: [{field: "state", op: "eq", value: "TX"}]},
    headers: {Origin: info.project.use.baseURL!}};
  const allTx = await page.request.post(`/api/dashboards/${seeded.id}/query`, txRequest);
  expect(allTx.status()).toBe(200);
  const allTxBody = await allTx.json();
  expect(allTxBody.scope).toBe("ALL");
  expect(allTxBody.results.sales.status).toBe("ok");
  expect(allTxBody.results.sales.rows[0].transaction_count).toBeGreaterThan(0);
  const conversationId = await openBuilder(page);
  // This preflight is free and must happen before the first paid /messages call.
  const preflight = await page.request.get(`/api/conversations/${conversationId}`);
  expect(preflight.status()).toBe(200);
  expect((await preflight.json()).mode).toBe("live");

  const evidence: {step: string; run_id: string; draft_revision: number; reused_query_ids: string[]}[] = [];
  const responses: LiveResponse[] = [];
  const checks: string[] = [];
  async function liveSend(step: string, request: string): Promise<LiveResponse> {
    const response = await send(page, request) as LiveResponse;
    responses.push(response);
    expectHealthy(response);
    evidence.push({step, run_id: response.run_id, draft_revision: response.draft_revision,
      reused_query_ids: response.reused_query_ids});
    await expect(page.getByText(`Bản nháp ${response.draft_revision}`, {exact: true})).toBeVisible();
    await expect(page.locator("main").getByRole("alert")).toHaveCount(0);
    return response;
  }

  try {
    const initial = await liveSend("create", "Tạo dashboard giá trị giao dịch năm 2025 theo tháng, bang và loại bất động sản.");
    expect(initial.draft_revision).toBe(1);
    expect(initial.draft.widgets).toHaveLength(6);
    const month = widget(initial.draft, "sales_value", "month");
    const state = widget(initial.draft, "sales_value", "state");
    const propertyType = widget(initial.draft, "sales_value", "property_type");
    const saleCard = widget(initial.draft, "sales_value", null);
    const transactionCard = widget(initial.draft, "transaction_count", null);
    const averageCard = widget(initial.draft, "average_sale_value", null);
    expect(month.chart).toBe("line");
    expect(state.chart).toBe("bar");
    expect(["bar", "donut", "pie"]).toContain(propertyType.chart);
    for (const card of [saleCard, transactionCard, averageCard]) expect(card.chart).toBe("card");
    for (const query of initial.draft.queries) {
      expect(query.plan.date_range).toEqual({start: "2025-01-01", end_exclusive: "2026-01-01"});
      expect(initial.sql[query.id]).toContain("SELECT");
    }
    await page.getByText("Nguồn KPI và SQL của bản nháp", {exact: true}).click();
    await expect(page.locator(".sqlPanel pre")).toHaveCount(initial.draft.queries.length);
    checks.push("initial-three-cards-three-charts-kpi-sql-dates");

    const california = await liveSend("filter-CA", "Lọc dashboard theo California.");
    expectStateAndDates(california.draft, "CA", "2025-01-01", "2026-01-01");
    expect(california.draft.widgets).toEqual(initial.draft.widgets);

    const horizontal = await liveSend("horizontal-property-type", "Đổi biểu đồ theo loại bất động sản thành cột ngang.");
    expect(horizontal.changes?.presentation_only).toBe(true);
    expect(new Set(horizontal.reused_query_ids)).toEqual(new Set(california.draft.queries.map(query => query.id)));
    expect(horizontal.draft.queries).toEqual(california.draft.queries);
    expect(widget(horizontal.draft, "sales_value", "property_type")).toEqual({...propertyType, chart: "horizontal_bar"});
    for (const original of california.draft.widgets.filter(item => item.id !== propertyType.id)) {
      expect(horizontal.draft.widgets.find(item => item.id === original.id)).toEqual(original);
    }

    const added = await liveSend("add-sold-property", "Thêm một thẻ số bất động sản đã có giao dịch.");
    const soldCard = widget(added.draft, "sold_property_count", null);
    expect(soldCard.chart).toBe("card");
    for (const original of horizontal.draft.widgets) {
      expect(added.draft.widgets.find(item => item.id === original.id)).toEqual(original);
    }

    const removed = await liveSend("remove-state-chart", "Bỏ biểu đồ theo bang.");
    expect(removed.draft.widgets.some(item => item.id === state.id)).toBe(false);
    for (const original of added.draft.widgets.filter(item => item.id !== state.id)) {
      expect(removed.draft.widgets.find(item => item.id === original.id)).toEqual(original);
    }

    const final = await liveSend("year-2024", "Đổi khoảng thời gian của dashboard sang năm 2024.");
    expect(final.draft_revision).toBe(6);
    expectStateAndDates(final.draft, "CA", "2024-01-01", "2025-01-01");
    expect(final.draft.widgets).toEqual(removed.draft.widgets);
    checks.push("five-edits-preserve-unrelated-widgets-and-query-reuse");
    await page.screenshot({path: info.outputPath("live-builder-preview.png"), fullPage: true});

    await restartBackend();
    await page.reload();
    await expect(page.getByText("Bản nháp 6", {exact: true})).toBeVisible();
    const restored = await (await page.request.get(`/api/conversations/${conversationId}`)).json();
    expect(restored.draft_hash).toBe(final.draft_hash);
    expect(restored.draft).toEqual(final.draft);
    expect(restored.response.results).toEqual(final.results);
    checks.push("real-backend-restart-preserves-draft-results-hash");

    await page.getByRole("button", {name: "Duyệt và phát hành", exact: true}).click();
    const published = page.getByRole("link", {name: "Mở dashboard phiên bản 1 →", exact: true});
    await expect(published).toBeVisible();
    const path = await published.getAttribute("href");
    expect(path).toMatch(/^\/dashboards\/[0-9a-f-]+$/);
    const dashboardId = path!.split("/").pop()!;
    const approval = {decision: "approve", draft_revision: final.draft_revision,
      draft_hash: final.draft_hash, base_version_number: final.base_version_number};
    const repeated = await page.request.post(`/api/conversations/${conversationId}/approval`, {
      headers: {Origin: info.project.use.baseURL!}, data: approval,
    });
    expect(repeated.status()).toBe(200);
    expect((await repeated.json()).version_number).toBe(1);
    expect((await repeated.json()).dashboard_id).toBe(dashboardId);
    expect((await (await page.request.get(`/api/dashboards/${dashboardId}`)).json()).version_number).toBe(1);
    checks.push("repeat-approval-remains-version-one");

    const viewer = await browser.newContext({baseURL: info.project.use.baseURL});
    try {
      const other = await viewer.newPage();
      await login(other, "viewer_ca");
      // The seeded dashboard has no saved CA filter: this control separates
      // Viewer scope from the CA AND TX saved-filter intersection below.
      const scopedTx = await other.request.post(`/api/dashboards/${seeded.id}/query`, txRequest);
      expect(scopedTx.status()).toBe(200);
      const scopedTxBody = await scopedTx.json();
      expect(scopedTxBody.scope).toBe("CA");
      for (const result of Object.values(scopedTxBody.results) as {status: string}[]) {
        expect(result.status).toBe("no_data");
      }
      await other.goto(path!);
      await expect(other.getByText(/Phạm vi: CA/)).toBeVisible();
      await expect(other.locator(".dashboardWidget")).toHaveCount(final.draft.widgets.length);
      await other.getByLabel("Bang", {exact: true}).fill("CA");
      await other.getByRole("button", {name: "Áp dụng bộ lọc", exact: true}).click();
      await expect(other.getByText("Không có dữ liệu trong kỳ và phạm vi đã chọn.", {exact: true})).toHaveCount(0);
      await other.getByLabel("Bang", {exact: true}).fill("TX");
      await other.getByRole("button", {name: "Áp dụng bộ lọc", exact: true}).click();
      await expect(other.getByText("Không có dữ liệu trong kỳ và phạm vi đã chọn.", {exact: true}))
        .toHaveCount(final.draft.widgets.length);
      await expect(other.locator("main").getByRole("alert")).toHaveCount(0);
      await other.screenshot({path: info.outputPath("live-viewer-empty-scope.png"), fullPage: true});
      checks.push("viewer-ca-filter-and-tx-empty-with-independent-scope-control");
      for (const [endpoint, data] of [
        ["/api/conversations", {}],
        [`/api/conversations/${conversationId}/messages`, {message: "Lọc Texas"}],
        [`/api/conversations/${conversationId}/approval`, approval],
      ] as const) {
        expect((await other.request.post(endpoint, {headers: {Origin: info.project.use.baseURL!}, data})).status()).toBe(403);
      }
      expect((await other.request.get(`/api/conversations/${conversationId}`)).status()).toBe(403);
      const foreign = await browser.newContext({baseURL: info.project.use.baseURL});
      try {
        const foreignPage = await foreign.newPage();
        await login(foreignPage, "builder_tx");
        expect((await foreignPage.request.get(`/api/conversations/${conversationId}`)).status()).toBe(404);
      } finally {await foreign.close();}
      checks.push("viewer-create-edit-approve-and-foreign-draft-denied");

      const beforeEdit = await (await other.request.get(`/api/dashboards/${dashboardId}`)).json();
      const editCreated = await page.request.post("/api/conversations", {
        headers: {Origin: info.project.use.baseURL!}, data: {dashboard_id: dashboardId},
      });
      expect(editCreated.status()).toBe(201);
      const editId = (await editCreated.json()).id;
      await page.goto(`/builder?conversation=${editId}`);
      const edited = await liveSend("edit-published-year-2025", "Đổi khoảng thời gian của dashboard sang năm 2025.");
      expectStateAndDates(edited.draft, "CA", "2025-01-01", "2026-01-01");
      expect(edited.draft.widgets).toEqual(final.draft.widgets);
      expect(await (await other.request.get(`/api/dashboards/${dashboardId}`)).json()).toEqual(beforeEdit);
      await page.getByRole("button", {name: "Duyệt và phát hành", exact: true}).click();
      await expect(page.getByRole("link", {name: "Mở dashboard phiên bản 2 →", exact: true})).toBeVisible();
      const afterEdit = await (await other.request.get(`/api/dashboards/${dashboardId}`)).json();
      expect(afterEdit.version_number).toBe(2);
      // Dashboard detail intentionally uses response_model_exclude_none;
      // conversation drafts retain explicit null optional fields.
      const publishedDraft = JSON.parse(JSON.stringify(edited.draft,
        (_key, value) => value === null ? undefined : value));
      expect(afterEdit.spec).toEqual(publishedDraft);
      checks.push("viewer-retains-version-one-until-second-approval");
    } finally {await viewer.close();}
  } finally {
    const evidencePath = info.outputPath("live-run-evidence.json");
    await writeFile(evidencePath, JSON.stringify({conversationId, evidence, responses, checks,
      complete: checks.length === 7}, null, 2));
    await info.attach("live-run-evidence.json", {path: evidencePath, contentType: "application/json"});
  }
});
