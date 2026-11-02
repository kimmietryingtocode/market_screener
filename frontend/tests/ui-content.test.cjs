const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");

const html = fs.readFileSync("index.html", "utf8");
const app = fs.readFileSync("app.js", "utf8");

test("authenticated UI has honest portfolio loading, empty, error, and saved states", () => {
  for (const id of ["portfolio-loading", "portfolio-empty", "portfolio-error-state", "portfolio-content"]) {
    assert.match(html, new RegExp(`id="${id}"`));
  }
});

test("signed-in UI contains no seeded market, portfolio, or backtest results", () => {
  const source = `${html}\n${app}`;
  for (const misleadingFixture of ["$128,420.62", "+68.4%", "6,735.13", "demoRankingData", "chartSeries", "mock quote preview"]) {
    assert.doesNotMatch(source, new RegExp(misleadingFixture.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i"));
  }
});

test("unsupported demo and testimonial paths are not offered", () => {
  const source = `${html}\n${app}`;
  assert.doesNotMatch(source, /demo data|testimonial|auth-proof/i);
});

test("authentication exposes only the official Google sign-in surface", () => {
  assert.match(html, /id="google-button"/);
  for (const removedId of ["auth-email", "auth-password", "auth-form", "auth-tabs", "auth-visual"]) {
    assert.doesNotMatch(html, new RegExp(`id="${removedId}"`));
  }
  assert.doesNotMatch(html, /forgot password|create account|sign up with email/i);
});

test("the three-step guide is persistent, reopenable, and contextual", () => {
  for (const id of ["open-guide", "guide-dialog", "guide-skip", "guide-back", "guide-next"]) {
    assert.match(html, new RegExp(`id="${id}"`));
  }
  assert.match(app, /SignalistOnboarding\.dismiss/);
  assert.match(app, /Create portfolio/);
  assert.match(app, /View portfolio/);
});

test("recommendations lead the signed-in flow and support FactSet refinement", () => {
  assert.ok(html.indexOf('id="rankings"') < html.indexOf('id="portfolio"'));
  assert.doesNotMatch(html, /id="rankings"[^>]*hidden/);
  for (const id of ["factset-form", "factset-files", "run-factset", "factset-status"]) {
    assert.match(html, new RegExp(`id="${id}"`));
  }
  assert.match(app, /ranking_stage/);
  assert.match(app, /FactSet validated/);
});

test("backtests have locked, empty, running, failed, and completed UI states", () => {
  for (const id of ["backtests-locked", "backtest-form", "backtest-history", "backtest-empty", "backtest-metrics", "equity-chart"]) {
    assert.match(html, new RegExp(`id="${id}"`));
  }
  assert.match(app, /Waiting for the backtest worker/);
  assert.match(app, /\["completed", "failed"\]/);
  assert.match(app, /setTimeout\(\(\) => refreshBacktestDetail\(detail\.id\), 3000\)/);
  assert.match(app, /document\.hidden/);
});

test("completed backtests render six real metrics and native SVG paths", () => {
  for (const key of ["total_return", "annualized_return", "volatility", "sharpe_ratio", "max_drawdown", "alpha_vs_benchmark"]) {
    assert.match(app, new RegExp(key));
  }
  assert.match(html, /<path id="portfolio-path"/);
  assert.match(html, /<path id="benchmark-path"/);
  assert.doesNotMatch(app, /progress\s*percent|progress_percentage/i);
});

test("starting investment explains its effect without backend terminology", () => {
  assert.match(html, /Starting investment \(\$\)/);
  assert.match(html, /Hypothetical cash invested on day one/);
  assert.doesNotMatch(html, />Starting capital</);
});
