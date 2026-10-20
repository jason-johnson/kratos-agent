import { test, expect, request } from "@playwright/test";
import { API_HEADERS, BACKEND_URL } from "./helpers";

test.describe("ARE eval scenarios", () => {
  test("scenarios endpoint returns at least one scenario", async () => {
    const api = await request.newContext({ extraHTTPHeaders: API_HEADERS });
    const resp = await api.get(`${BACKEND_URL}/api/evals/scenarios`);
    expect(resp.status(), "scenarios status").toBe(200);
    const body = await resp.json();
    expect(body, "scenarios body shape").toHaveProperty("scenarios");
    const scenarios = body.scenarios as unknown[];
    expect(scenarios.length, "should have at least one scenario").toBeGreaterThan(0);

    const first = scenarios[0] as Record<string, unknown>;
    expect(first.name, "scenario.name").toBeTruthy();
    expect(first.input_message, "scenario.input_message").toBeTruthy();
  });
});
