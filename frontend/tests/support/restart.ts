import {randomUUID} from "node:crypto";
import {readFile, writeFile} from "node:fs/promises";
import path from "node:path";
import {expect} from "@playwright/test";

export async function restartBackend(fault: "none" | "reader" | "provider" = "none") {
  const control = process.env.E2E_CONTROL_DIRECTORY;
  if (!control) throw new Error("run this test with test_local_postgres.py --e2e");
  const id = randomUUID();
  const action = fault === "none" ? "restart_backend" : `restart_backend_with_${fault}_outage`;
  await writeFile(path.join(control, "restart-request.json"), JSON.stringify({action, id}));
  let restarted: {id: string; status: string; old_pid: number; new_pid: number} | null = null;
  await expect.poll(async () => {
    try {
      restarted = JSON.parse(await readFile(path.join(control, "restart-completed.json"), "utf8"));
      return restarted?.id === id ? restarted?.status : "waiting";
    } catch {return "waiting";}
  }, {timeout: 25_000}).toBe("ready");
  expect(restarted!.old_pid).not.toBe(restarted!.new_pid);
}
