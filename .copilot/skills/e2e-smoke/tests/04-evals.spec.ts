import { test, expect, request } from "@playwright/test";
import { API_HEADERS, BACKEND_URL } from "./helpers";

test.describe("evals runs", () => {
  test("at least one ARE run exists and its detail is reachable", async () => {
    const api = await request.newContext({ extraHTTPHeaders: API_HEADERS });
    const resp = await api.get(`${BACKEND_URL}/api/evals/runs`);
    expect(resp.status(), "runs status").toBe(200);
    const body = await resp.json();
    const runs: any[] = body.runs ?? [];
    expect(runs.length, "ARE run count").toBeGreaterThan(0);

    const runId = runs[0].run_id;
    const detail = await api.get(`${BACKEND_URL}/api/evals/runs/${runId}`);
    expect(detail.status(), `${runId} detail status`).toBe(200);
    const detailBody = await detail.json();
    expect(detailBody, `${runId} detail shape`).toHaveProperty("run_id", runId);
  });
});
