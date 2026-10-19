// Browser timing worker. Credentials stay in environment; stdout is metrics only.
import {chromium, expect} from "@playwright/test";
import {performance} from "node:perf_hooks";

let input = "";
for await (const chunk of process.stdin) input += chunk;
const config = JSON.parse(input);
const browser = await chromium.launch();
const groups = {};
let stage = "startup";

async function login(page, username) {
  stage = "login";
  await page.goto(`${config.base_url}/login`);
  await page.getByLabel("Tên đăng nhập").fill(username);
  await page.getByLabel("Mật khẩu").fill(process.env.BENCHMARK_PASSWORD ?? "");
  await page.getByRole("button", {name: "Đăng nhập", exact: true}).click();
  await expect(page).toHaveURL(/\/dashboards$/, {timeout: 15_000});
  const response = await page.request.get(`${config.base_url}/api/auth/me`);
  if (!response.ok()) throw new Error("authentication_failed");
  return response.json();
}

function watchApi(page, suffix) {
  const pending = page.waitForResponse(response => response.url().endsWith(suffix) && response.request().method() === "POST")
    .then(async response => {
      await response.finished();
      const finished = performance.now();
      if (!response.ok()) throw new Error("api_failed");
      return {body: await response.json(), finished, api_ms: response.request().timing().responseEnd};
    });
  pending.catch(() => {}); // navigation failure still records an error sample
  return pending;
}

async function rendered(page, spec, results) {
  const widgets = spec.widgets;
  await expect(page.locator(".dashboardGrid")).toHaveAttribute("aria-busy", "false", {timeout: 15_000});
  await expect(page.locator(".dashboardWidget")).toHaveCount(widgets.length);
  const canvases = widgets.filter(widget => !["card", "table"].includes(widget.chart) && results[widget.query_id]?.status === "ok").length;
  await expect(page.locator("canvas")).toHaveCount(canvases, {timeout: 15_000});
  if (await page.locator(".dashboardWidget .authError").count()) throw new Error("widget_failed");
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}

try {
  for (const group of config.groups) {
    const context = await browser.newContext({viewport: {width: 1440, height: 1000}});
    const page = await context.newPage();
    page.setDefaultTimeout(90_000);
    const actor = await login(page, group === "saved" ? config.saved_user : config.agent_user);
    let detail;
    if (group === "saved") {
      const listing = await (await page.request.get(`${config.base_url}/api/dashboards`)).json();
      const id = config.dashboard_id ?? listing[0]?.id;
      if (!id) throw new Error("no_published_dashboard");
      const response = await page.request.get(`${config.base_url}/api/dashboards/${id}`);
      if (!response.ok()) throw new Error("dashboard_unavailable");
      detail = await response.json();
    } else if (actor.role !== "builder") throw new Error("builder_required");
    groups[group] = {role: actor.role, scope: actor.scope_profile, samples: [], warmup: []};
    for (let iteration = 0; iteration < config.warmup + config.samples; iteration++) {
      stage = group;
      let started;
      let attemptedRun = null;
      try {
        let api, spec, results;
        if (group === "saved") {
          const pending = watchApi(page, "/query");
          started = performance.now();
          await page.goto(`${config.base_url}/dashboards/${detail.id}`);
          api = await pending;
          if (api.body.version_number !== detail.version_number) throw new Error("dashboard_changed_during_benchmark");
          spec = detail.spec;
          results = api.body.results;
        } else {
          await page.goto(`${config.base_url}/builder`);
          await expect(page).toHaveURL(/\/builder\?conversation=/);
          await expect(page.getByLabel("Nội dung yêu cầu")).toBeEnabled();
          const id = new URL(page.url()).searchParams.get("conversation");
          const conversation = await (await page.request.get(`${config.base_url}/api/conversations/${id}`)).json();
          // Guard the actual server before the first potential paid call.
          if (conversation.mode !== config.expected_mode) throw new Error("mode_mismatch");
          await page.getByLabel("Nội dung yêu cầu").fill(config.prompt);
          const pending = watchApi(page, "/messages");
          started = performance.now();
          await page.getByRole("button", {name: "Gửi yêu cầu", exact: true}).click();
          api = await pending;
          attemptedRun = api.body.run_id;
          if (api.body.mode !== config.expected_mode || api.body.status !== "awaiting_approval") throw new Error("agent_failed");
          spec = api.body.draft;
          results = api.body.results;
          await expect(page.getByRole("button", {name: "Duyệt và phát hành", exact: true})).toBeEnabled({timeout: 15_000});
        }
        if (Object.values(results).some(result => result.status === "error")) throw new Error("query_failed");
        await rendered(page, spec, results);
        const finished = performance.now();
        const measurement = {ok: true, total_ms: finished - started, api_ms: api.api_ms,
          render_after_api_ms: Math.max(0, finished - api.finished),
          db_ms: Object.values(results).reduce((sum, result) => sum + result.latency_ms, 0),
          query_count: Object.keys(results).length, run_id: api.body.run_id ?? null, mode: api.body.mode ?? null};
        groups[group][iteration < config.warmup ? "warmup" : "samples"].push(measurement);
      } catch (error) {
        // Error messages may contain submitted credentials or provider content.
        // Keep only a bounded stage/type; failed samples remain in the report.
        groups[group][iteration < config.warmup ? "warmup" : "samples"].push({ok: false,
          total_ms: started ? performance.now() - started : null, stage: group, error_type: error.constructor.name, run_id: attemptedRun});
        if (!started) throw error; // setup/mode guard failures must not retry calls
      }
    }
    await context.close();
  }
  process.stdout.write(JSON.stringify({groups, browser_version: browser.version()}));
} catch (error) {
  process.stdout.write(JSON.stringify({groups, fatal: {stage, error_type: error.constructor.name}}));
  process.exitCode = 2;
} finally {await browser.close();}
