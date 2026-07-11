import { afterEach, describe, expect, it, vi } from "vitest";
import { api, API_BASE_URL } from "./api";

// Default mock fetch returns empty 200 — individual tests override as needed.
function mockFetch(response: Response) {
  return vi.spyOn(globalThis, "fetch").mockResolvedValue(response);
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.restoreAllMocks();
  sessionStorage.clear();
});

// --------------------------------------------------------------------------- //
// API_BASE_URL
// --------------------------------------------------------------------------- //

describe("API_BASE_URL", () => {
  it("is a non-empty URL string", () => {
    expect(API_BASE_URL).toBeTruthy();
    expect(API_BASE_URL).toMatch(/^https?:\/\//);
  });
});

// --------------------------------------------------------------------------- //
// qs (tested indirectly through api methods)
// --------------------------------------------------------------------------- //

describe("query string construction", () => {
  it("omits undefined params", async () => {
    const f = mockFetch(jsonResponse([]));
    await api.modelsSummary("proj", "field");
    expect(f).toHaveBeenCalledWith(
      `${API_BASE_URL}/api/projects/proj/fields/field/models-summary`,
      expect.anything(),
    );
  });

  it("includes defined params", async () => {
    const f = mockFetch(jsonResponse([]));
    await api.modelsSummary("proj", "field", 3);
    expect(f).toHaveBeenCalledWith(
      `${API_BASE_URL}/api/projects/proj/fields/field/models-summary?prompt_version=3`,
      expect.anything(),
    );
  });

  it("encodes special characters in params", async () => {
    const f = mockFetch(jsonResponse([]));
    await api.iterations("proj", "field", "openai/gpt-4");
    expect(f).toHaveBeenCalledWith(
      `${API_BASE_URL}/api/projects/proj/fields/field/iterations?model_id=openai%2Fgpt-4`,
      expect.anything(),
    );
  });

  it("includes multiple params", async () => {
    const f = mockFetch(jsonResponse({}));
    await api.confusion("proj", "field", "openai/gpt-4", 2);
    const url = f.mock.calls[0][0] as string;
    expect(url).toContain("model_id=openai%2Fgpt-4");
    expect(url).toContain("prompt_version=2");
    expect(url).toContain("?");
  });

  it("omits null params", async () => {
    const f = mockFetch(jsonResponse({}));
    await api.confusion("proj", "field", undefined, undefined);
    const url = f.mock.calls[0][0] as string;
    expect(url).not.toContain("?");
  });
});

// --------------------------------------------------------------------------- //
// Auth token
// --------------------------------------------------------------------------- //

describe("auth token", () => {
  it("attaches Bearer token when present in sessionStorage", async () => {
    sessionStorage.setItem("promptlab_token", "abc123");
    const f = mockFetch(jsonResponse([]));
    await api.projects();
    const init = f.mock.calls[0][1] as RequestInit;
    expect(init.headers).toEqual({ Authorization: "Bearer abc123" });
  });

  it("omits Authorization header when no token", async () => {
    const f = mockFetch(jsonResponse([]));
    await api.projects();
    const init = f.mock.calls[0][1] as RequestInit;
    expect(init.headers).toBeUndefined();
  });
});

// --------------------------------------------------------------------------- //
// Error handling
// --------------------------------------------------------------------------- //

describe("error handling", () => {
  it("throws on non-200 response", async () => {
    mockFetch(jsonResponse({ detail: "not found" }, 404));
    await expect(api.projects()).rejects.toThrow("API error 404");
  });

  it("throws on 500 response", async () => {
    mockFetch(jsonResponse({ detail: "server error" }, 500));
    await expect(api.thresholds()).rejects.toThrow("API error 500");
  });
});

// --------------------------------------------------------------------------- //
// URL paths for each endpoint
// --------------------------------------------------------------------------- //

describe("endpoint URL paths", () => {
  it("projects", async () => {
    const f = mockFetch(jsonResponse([]));
    await api.projects();
    expect(f.mock.calls[0][0]).toBe(`${API_BASE_URL}/api/projects`);
  });

  it("fields", async () => {
    const f = mockFetch(jsonResponse([]));
    await api.fields("dep");
    expect(f.mock.calls[0][0]).toBe(`${API_BASE_URL}/api/projects/dep/fields`);
  });

  it("promptVersions", async () => {
    const f = mockFetch(jsonResponse([]));
    await api.promptVersions("dep", "authors");
    expect(f.mock.calls[0][0]).toBe(
      `${API_BASE_URL}/api/projects/dep/fields/authors/prompt-versions`,
    );
  });

  it("thresholds", async () => {
    const f = mockFetch(jsonResponse({}));
    await api.thresholds();
    expect(f.mock.calls[0][0]).toBe(`${API_BASE_URL}/api/config/thresholds`);
  });

  it("jobs", async () => {
    const f = mockFetch(jsonResponse([]));
    await api.jobs("dep", "field");
    expect(f.mock.calls[0][0]).toBe(
      `${API_BASE_URL}/api/projects/dep/fields/field/jobs`,
    );
  });

  it("activity with default logLines", async () => {
    const f = mockFetch(jsonResponse({}));
    await api.activity();
    expect(f.mock.calls[0][0]).toBe(`${API_BASE_URL}/api/activity?log_lines=30`);
  });

  it("activity with custom logLines", async () => {
    const f = mockFetch(jsonResponse({}));
    await api.activity(100);
    expect(f.mock.calls[0][0]).toBe(`${API_BASE_URL}/api/activity?log_lines=100`);
  });

  it("stageStatus", async () => {
    const f = mockFetch(jsonResponse({}));
    await api.stageStatus("dep", "field", 5);
    expect(f.mock.calls[0][0]).toBe(
      `${API_BASE_URL}/api/projects/dep/fields/field/stage-status?prompt_version=5`,
    );
  });

  it("runVersions", async () => {
    const f = mockFetch(jsonResponse([]));
    await api.runVersions("dep", "field");
    expect(f.mock.calls[0][0]).toBe(
      `${API_BASE_URL}/api/projects/dep/fields/field/run-versions`,
    );
  });

  it("llmJudgeSummary", async () => {
    const f = mockFetch(jsonResponse([]));
    await api.llmJudgeSummary("dep", "field", 2);
    expect(f.mock.calls[0][0]).toBe(
      `${API_BASE_URL}/api/projects/dep/fields/field/llm-judge-summary?prompt_version=2`,
    );
  });

  it("crossModelAgreement", async () => {
    const f = mockFetch(jsonResponse([]));
    await api.crossModelAgreement("dep", "field", 3);
    expect(f.mock.calls[0][0]).toBe(
      `${API_BASE_URL}/api/projects/dep/fields/field/cross-model-agreement?prompt_version=3`,
    );
  });

  it("selfConsistency", async () => {
    const f = mockFetch(jsonResponse([]));
    await api.selfConsistency("dep", "field");
    expect(f.mock.calls[0][0]).toBe(
      `${API_BASE_URL}/api/projects/dep/fields/field/self-consistency`,
    );
  });

  it("calibration", async () => {
    const f = mockFetch(jsonResponse([]));
    await api.calibration("dep", "field", 1);
    expect(f.mock.calls[0][0]).toBe(
      `${API_BASE_URL}/api/projects/dep/fields/field/calibration?prompt_version=1`,
    );
  });
});

// --------------------------------------------------------------------------- //
// JSON parsing
// --------------------------------------------------------------------------- //

describe("JSON parsing", () => {
  it("returns parsed JSON body", async () => {
    const data = [{ model_id: "a", n: 10 }];
    mockFetch(jsonResponse(data));
    const result = await api.modelsSummary("dep", "field");
    expect(result).toEqual(data);
  });

  it("init object has signal property (even if undefined)", async () => {
    // The api methods don't forward AbortSignal, but getJson always sets
    // { signal, headers } on the fetch init — so the key exists.
    const f = vi.fn().mockResolvedValue(jsonResponse([]));
    vi.stubGlobal("fetch", f);
    await api.projects();
    const init = f.mock.calls[0]?.[1] as Record<string, unknown> | undefined;
    expect(init).toBeDefined();
    expect(init).toHaveProperty("signal");
  });
});
