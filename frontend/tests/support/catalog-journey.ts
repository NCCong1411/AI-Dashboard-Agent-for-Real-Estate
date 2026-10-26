import {expect, Page, TestInfo} from "@playwright/test";
import {writeFile} from "node:fs/promises";
import {login, openBuilder, send} from "./session";

type Widget = {id: string; kpi: string; chart: string; dimension: string | null; query_id: string};
type Query = {id: string; plan: {kpis: string[]; dimensions: string[];
  date_range: {start: string | null; end_exclusive: string | null}; filters: unknown[]}};
type Draft = {widgets: Widget[]; queries: Query[]; layout: {widget_id: string}[]};
type Response = {outcome: string; status: string; mode: string; message: string; run_id: string;
  draft: Draft | null; draft_revision: number; sql: Record<string, string>;
  results: Record<string, {status: string}>; reused_query_ids: string[]};

/** Same acceptance journey for deterministic regression and opted-in live API. */
export async function catalogJourney(page: Page, info: TestInfo, mode: "mock" | "live") {
  await login(page, "builder_all");
  const conversation = await openBuilder(page);
  const preflight = await (await page.request.get(`/api/conversations/${conversation}`)).json();
  expect(preflight.mode).toBe(mode);
  const evidence: {prompt: string; response: Response}[] = [];
  async function step(prompt: string): Promise<Response & {draft: Draft}> {
    const response = await send(page, prompt) as Response;
    evidence.push({prompt, response});
    expect(response.mode).toBe(mode);
    expect(response.status).toBe("awaiting_approval");
    expect(response.outcome).toBe("plan");
    expect(response.draft).not.toBeNull();
    for (const result of Object.values(response.results)) expect(result.status).toBe("ok");
    for (const query of response.draft!.queries) {
      expect(query.plan.date_range).toEqual({start: "2025-01-01", end_exclusive: "2026-01-01"});
    }
    return response as Response & {draft: Draft};
  }
  try {
    const created = await step("Tạo dashboard tổng giá trị giao dịch theo bang năm 2025");
    const salesCharts = created.draft.widgets.filter(item => item.kpi === "sales_value" && item.chart !== "card");
    expect(salesCharts).toHaveLength(1);
    const state = salesCharts[0];
    expect(state.dimension).toBe("state");
    expect(created.sql[state.query_id]).toContain("analytics.sales");
    const added = await step("Thêm số lượng đề nghị mua bất động sản");
    const offer = added.draft.widgets.filter(item => item.kpi === "offer_count");
    expect(offer).toHaveLength(1);
    expect(offer[0].chart).toBe("card");
    const offersQuery = added.draft.queries.find(item => item.id === offer[0].query_id)!;
    expect(offersQuery.plan.kpis).toEqual(["offer_count"]);
    expect(added.sql[offersQuery.id]).toContain("analytics.offers");
    expect(added.sql[offersQuery.id]).not.toMatch(/\bJOIN\b/i);
    for (const original of created.draft.widgets) expect(added.draft.widgets.find(item => item.id === original.id)).toEqual(original);
    const horizontal = await step("Đổi biểu đồ doanh số thành cột ngang");
    expect(horizontal.draft.widgets.find(item => item.id === state.id)).toEqual({...state, chart: "horizontal_bar"});
    expect(horizontal.draft.queries).toEqual(added.draft.queries);
    expect(new Set(horizontal.reused_query_ids)).toEqual(new Set(added.draft.queries.map(item => item.id)));
    for (const original of added.draft.widgets.filter(item => item.id !== state.id)) {
      expect(horizontal.draft.widgets.find(item => item.id === original.id)).toEqual(original);
    }
    await expect(page.locator("canvas")).toHaveCount(1);
    const removed = await step("Bỏ biểu đồ theo bang");
    expect(removed.draft.widgets.some(item => item.id === state.id)).toBe(false);
    for (const original of horizontal.draft.widgets.filter(item => item.id !== state.id)) {
      expect(removed.draft.widgets.find(item => item.id === original.id)).toEqual(original);
    }
    const unsupportedPrompt = "Thêm số lượt khách xem tin bất động sản trên website";
    const unsupported = await send(page, unsupportedPrompt) as Response;
    evidence.push({prompt: unsupportedPrompt, response: unsupported});
    expect(unsupported.outcome).toBe("unsupported");
    expect(unsupported.message).toMatch(/dữ liệu|nguồn|hỗ trợ|catalog|mô hình/i);
    expect(unsupported.message).toMatch(/lượt|xem|website|truy cập/i);
    expect(unsupported.draft).toBeNull();
    expect(unsupported.results).toEqual({});
    expect(unsupported.sql).toEqual({});
    const restored = await (await page.request.get(`/api/conversations/${conversation}`)).json();
    expect(restored.draft).toEqual(removed.draft);
    expect(restored.draft_revision).toBe(removed.draft_revision);
    await expect(page.locator(".dashboardWidget")).toHaveCount(removed.draft.widgets.length);
    await page.screenshot({path: info.outputPath(`catalog-${mode}-preserved-preview.png`), fullPage: true});
  } finally {
    await writeFile(info.outputPath("catalog-journey.json"), JSON.stringify({mode, conversation, evidence}, null, 2));
  }
}
