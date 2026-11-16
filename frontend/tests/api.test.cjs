const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");
const vm = require("node:vm");

function clientWith(response = { ok: true, status: 200, json: async () => ({ token: "app-token" }) }) {
  const calls = [];
  class FakeFormData {
    constructor() { this.entries = []; }
    append(key, value) { this.entries.push([key, value]); }
  }
  const context = {
    FormData: FakeFormData,
    fetch: async (url, options) => {
      calls.push({ url, options });
      return response;
    },
    window: { SignalistConfig: { apiBaseUrl: "http://localhost:8081/" } },
  };
  vm.runInNewContext(fs.readFileSync("api.js", "utf8"), context);
  return { api: context.window.SignalistApi, calls };
}

test("Google login sends the credential to FastAPI for verification", async () => {
  const { api, calls } = clientWith();

  await api.googleLogin("google-id-token");

  assert.equal(calls[0].url, "http://localhost:8081/api/v1/auth/google");
  assert.deepEqual(JSON.parse(calls[0].options.body), { credential: "google-id-token" });
});

test("Google configuration is loaded from FastAPI", async () => {
  const { api, calls } = clientWith({
    ok: true,
    status: 200,
    json: async () => ({ enabled: true, client_id: "client.apps.googleusercontent.com" }),
  });

  const config = await api.getGoogleConfig();

  assert.equal(calls[0].url, "http://localhost:8081/api/v1/auth/google/config");
  assert.equal(config.enabled, true);
});

test("FactSet summaries upload as authenticated multipart data", async () => {
  const { api, calls } = clientWith();
  const files = [{ name: "one.csv" }, { name: "two.csv" }];

  await api.importFactSet("app-token", files);

  assert.equal(calls[0].url, "http://localhost:8081/api/v1/rankings/factset");
  assert.equal(calls[0].options.method, "POST");
  assert.equal(calls[0].options.headers.Authorization, "Bearer app-token");
  assert.equal(calls[0].options.headers["Content-Type"], undefined);
  assert.deepEqual(calls[0].options.body.entries, [["files", files[0]], ["files", files[1]]]);
});

test("portfolio creation sends a bearer token and weights summing to one", async () => {
  const { api, calls } = clientWith();

  await api.createPortfolio("app-token", "Core", ["AAPL", "MSFT", "NVDA"]);

  assert.equal(calls[0].options.headers.Authorization, "Bearer app-token");
  const payload = JSON.parse(calls[0].options.body);
  assert.equal(payload.holdings.reduce((total, holding) => total + holding.weight, 0), 1);
  assert.deepEqual(payload.holdings.map((holding) => holding.ticker), ["AAPL", "MSFT", "NVDA"]);
});

test("portfolio editing replaces holdings through the owned portfolio route", async () => {
  const { api, calls } = clientWith();

  await api.updatePortfolio("app-token", 42, "Updated", ["AAPL", "JPM"]);

  assert.equal(calls[0].url, "http://localhost:8081/api/v1/portfolios/42");
  assert.equal(calls[0].options.method, "PUT");
  assert.equal(calls[0].options.headers.Authorization, "Bearer app-token");
  const payload = JSON.parse(calls[0].options.body);
  assert.equal(payload.name, "Updated");
  assert.equal(payload.holdings.reduce((total, holding) => total + holding.weight, 0), 1);
});

test("API errors expose FastAPI validation messages", async () => {
  const response = {
    ok: false,
    status: 422,
    json: async () => ({ detail: [{ msg: "Start date must be before end date" }] }),
  };
  const { api } = clientWith(response);

  await assert.rejects(api.createBacktest("token", {}), /Start date must be before end date/);
});

test("backtest creation sends the exact request with authorization", async () => {
  const { api, calls } = clientWith();
  const payload = {
    portfolio_id: 12,
    benchmark: "SPY",
    start_date: "2021-01-01",
    end_date: "2026-01-01",
    rebalance_frequency: "monthly",
    initial_capital: 100000,
    cost_bps: 10,
  };

  await api.createBacktest("app-token", payload);

  assert.equal(calls[0].url, "http://localhost:8081/api/v1/backtests");
  assert.equal(calls[0].options.method, "POST");
  assert.equal(calls[0].options.headers.Authorization, "Bearer app-token");
  assert.deepEqual(JSON.parse(calls[0].options.body), payload);
});

test("backtest history and detail use protected routes", async () => {
  const { api, calls } = clientWith();

  await api.listBacktests("app-token");
  await api.getBacktest("app-token", 17);

  assert.equal(calls[0].url, "http://localhost:8081/api/v1/backtests");
  assert.equal(calls[1].url, "http://localhost:8081/api/v1/backtests/17");
  assert.equal(calls[0].options.headers.Authorization, "Bearer app-token");
  assert.equal(calls[1].options.headers.Authorization, "Bearer app-token");
});

test("API errors retain the HTTP status for expired-session handling", async () => {
  const { api } = clientWith({
    ok: false,
    status: 401,
    json: async () => ({ detail: "Invalid token" }),
  });

  await assert.rejects(api.listPortfolios("expired"), (error) => {
    assert.equal(error.status, 401);
    return true;
  });
});
