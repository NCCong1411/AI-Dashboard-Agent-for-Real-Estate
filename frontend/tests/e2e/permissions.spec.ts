import {randomUUID} from "node:crypto";
import {expect, test} from "@playwright/test";
import {login, openBuilder, send} from "../support/session";

test("Viewer and foreign Builder cannot read, edit or approve another draft", async ({page, browser}) => {
  await login(page, "builder_all");
  const id = await openBuilder(page);
  const draft = await send(page, "Tổng quan giao dịch năm 2025");
  const origin = new URL(page.url()).origin;
  for (const [account, status] of [["viewer_ca", 403], ["builder_tx", 404]] as const) {
    const context = await browser.newContext();
    try {
      const other = await context.newPage();
      await login(other, account);
      expect((await context.request.get(`/api/conversations/${id}`)).status()).toBe(status);
      expect((await context.request.post(`/api/conversations/${id}/messages`, {
        headers: {Origin: origin}, data: {message: "Lọc Texas"},
      })).status()).toBe(status);
      expect((await context.request.post(`/api/conversations/${id}/approval`, {headers: {Origin: origin}, data: {
        decision: "approve", draft_revision: draft.draft_revision, draft_hash: draft.draft_hash,
        base_version_number: draft.base_version_number,
      }})).status()).toBe(status);
      if (account === "viewer_ca") {
        await other.goto(`/builder?conversation=${id}`);
        await expect(other).toHaveURL(/\/dashboards$/);
      } else {
        expect((await context.request.get(`/api/conversations/${randomUUID()}`)).status()).toBe(404);
      }
    } finally {await context.close();}
  }
  await page.reload();
  await expect(page.getByText("Bản nháp 1", {exact: true})).toBeVisible();
});
