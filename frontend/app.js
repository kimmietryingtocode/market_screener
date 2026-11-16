let session = window.SignalistSession.load();
let portfolios = [];
let currentPortfolio = null;
let selectedStocks = [];
let rankingData = [];
let selectedBacktestId = null;
let selectedBacktestDetail = null;
let backtestPollTimer = null;
let aiReportId = null;
let aiPollTimer = null;
let guideIndex = 0;

const $ = (selector) => document.querySelector(selector);
const authView = $("#auth-view");
const appShell = $("#app-shell");
const rankingRows = $("#ranking-rows");
const stockDialog = $("#stock-dialog");
const guideDialog = $("#guide-dialog");
const toast = $("#toast");

const guideSlides = [
  { number: "1", title: "Start with the ranking", copy: "Review the companies surfaced by the latest published market screen." },
  { number: "2", title: "Build your portfolio", copy: "Use ranked companies or add your own tickers. Your first allocation starts equally weighted." },
  { number: "3", title: "Confirm with a backtest", copy: "Test a recommended portfolio or your own selection using real worker results." },
];

function setError(element, message = "") {
  element.textContent = message;
  element.hidden = !message;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;",
  })[character]);
}

function initials(value) {
  return value.trim().split(/\s+|@/).filter(Boolean).slice(0, 2)
    .map((word) => word[0].toUpperCase()).join("") || "SI";
}

function displayName() {
  return session?.displayName || session?.email?.split("@")[0] || "there";
}

function formatDate(value) {
  if (!value) return "—";
  const suffix = /^\d{4}-\d{2}-\d{2}$/.test(value) ? "T00:00:00" : "";
  return new Intl.DateTimeFormat("en-US", { dateStyle: "medium" }).format(new Date(`${value}${suffix}`));
}

function inputDate(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

let toastTimer;
function showToast(message) {
  toast.textContent = message;
  toast.classList.add("visible");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("visible"), 2500);
}

function setAuthStatus(message, { error = "", retry = false, showButton = false } = {}) {
  $("#auth-status").textContent = message;
  $("#google-button").hidden = !showButton;
  $("#retry-google").hidden = !retry;
  setError($("#auth-error"), error);
}

async function mountGoogleSignIn() {
  setAuthStatus("Preparing Google sign-in…");
  try {
    const config = await window.SignalistApi.getGoogleConfig();
    if (!config.enabled || !config.client_id) {
      throw new Error("Google sign-in is not configured. Add GOOGLE_CLIENT_ID to the root .env and restart the API.");
    }
    await window.SignalistGoogleAuth.mount({
      clientId: config.client_id,
      container: $("#google-button"),
      onCredential: handleGoogleCredential,
      onClick: () => setAuthStatus("Complete sign-in in the Google window.", { showButton: true }),
    });
    setAuthStatus("", { showButton: true });
  } catch (error) {
    setAuthStatus("Google sign-in is unavailable.", { error: error.message, retry: true });
  }
}

async function handleGoogleCredential(response) {
  if (!response?.credential) {
    setAuthStatus("Google did not return a credential.", { error: "Please try again.", retry: true, showButton: true });
    return;
  }
  setAuthStatus("Signing you in…");
  try {
    const auth = await window.SignalistApi.googleLogin(response.credential);
    session = window.SignalistSession.save(auth);
    await showApp();
  } catch (error) {
    setAuthStatus("Google sign-in could not be completed.", { error: error.message, retry: true, showButton: true });
  }
}

function stopBacktestPolling() {
  clearTimeout(backtestPollTimer);
  backtestPollTimer = null;
}

function showAuth(message = "") {
  stopBacktestPolling();
  if (guideDialog.open) guideDialog.close();
  authView.hidden = false;
  appShell.hidden = true;
  setAuthStatus(message || "Preparing Google sign-in…", { error: message });
  mountGoogleSignIn();
}

async function showApp() {
  authView.hidden = true;
  appShell.hidden = false;
  const identity = session.displayName || session.email || "Account";
  $("#profile-initials").textContent = initials(identity);
  $("#profile-name").textContent = session.displayName || "Account";
  $("#profile-email").textContent = session.email || "";
  $("#welcome-title").textContent = `Welcome, ${displayName()}.`;
  $("#welcome-message").textContent = "Loading the latest recommendations and your portfolio…";
  const [loaded] = await Promise.all([loadPortfolios(), loadRankings()]);
  if (loaded) maybeOpenGuide();
}

function expireSession(error) {
  if (error.status !== 401) return false;
  window.SignalistSession.clear();
  session = null;
  portfolios = [];
  currentPortfolio = null;
  selectedBacktestId = null;
  showAuth("Your session expired. Sign in again.");
  return true;
}

function showPortfolioState(state, errorMessage = "") {
  ["loading", "empty", "error-state", "content"].forEach((name) => {
    $(`#portfolio-${name}`).hidden = name !== state;
  });
  if (state === "error-state") $("#portfolio-load-error").textContent = errorMessage;
}

function renderPortfolio(portfolio) {
  currentPortfolio = portfolio;
  const holdings = portfolio.holdings || [];
  $("#portfolio-name-display").textContent = portfolio.name;
  $("#portfolio-created").textContent = portfolio.created_at ? `Created ${formatDate(portfolio.created_at)}` : "Saved portfolio";
  $("#portfolio-position-count").textContent = `${holdings.length} ${holdings.length === 1 ? "holding" : "holdings"}`;
  $("#position-chips").innerHTML = holdings.map((holding) => (
    `<span><strong>${escapeHtml(holding.ticker)}</strong><small>${Math.round(holding.weight * 100)}%</small></span>`
  )).join("");
  showPortfolioState("content");
}

function setProgression(hasPortfolio) {
  $("#backtests-locked").hidden = hasPortfolio;
  $("#backtest-workspace").hidden = !hasPortfolio;
  if (!hasPortfolio) {
    stopBacktestPolling();
    $("#welcome-message").textContent = "Review our ranked companies, then build a portfolio from them or choose your own.";
    return;
  }
  $("#welcome-message").textContent = "Compare the recommendations with your holdings, then confirm the idea with a backtest.";
  populateBacktestPortfolios();
  setBacktestDefaults();
  loadBacktests();
}

async function loadPortfolios() {
  showPortfolioState("loading");
  try {
    portfolios = await window.SignalistApi.listPortfolios(session.token);
    currentPortfolio = portfolios[0] || null;
    if (currentPortfolio) renderPortfolio(currentPortfolio);
    else showPortfolioState("empty");
    setProgression(Boolean(currentPortfolio));
    return true;
  } catch (error) {
    if (expireSession(error)) return false;
    showPortfolioState("error-state", error.message);
    $("#welcome-message").textContent = "Your account is signed in, but portfolio data could not load.";
    setProgression(false);
    return false;
  }
}

function score(value) {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? Math.min(100, Math.max(0, parsed)) : null;
}

function scoreBreakdown(stock) {
  // scores.components records the raw inputs behind every rank. The API has
  // always returned it; nothing displayed it, so a rank was a number with no
  // stated reason.
  const c = stock.components || {};
  const pct = (v) => metricValue(v, true);
  const num = (v) => (Number.isFinite(v) ? Number(v).toFixed(2) : "—");
  const rows = [
    ["Composite", num(stock.score)],
    ["Momentum rank", num(stock.momentum)],
    ["Fundamental rank", stock.fundamentals === null ? "not scored" : num(stock.fundamentals)],
    ["Trailing return", pct(c.trailing_return)],
    ["Revenue growth", pct(c.revenue_growth)],
    ["Operating margin", pct(c.operating_margin)],
    ["Debt / equity", num(c.debt_to_equity)],
  ];
  const used = Number.isFinite(c.fundamental_inputs_used) ? c.fundamental_inputs_used : null;
  const caveat = used === 0
    ? "No fundamentals available, so the fundamental score defaults to a neutral 50 rather than penalising the company for missing data."
    : `Ranks are percentiles within the ${stock.methodologyVersion || "current"} universe, not absolute quality.`;
  return `<div class="score-why"><strong>Why ${escapeHtml(stock.ticker)} ranks ${stock.rank}</strong>
    <dl>${rows.map(([k, v]) => `<div><dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd></div>`).join("")}</dl>
    <p class="chart-note">${escapeHtml(caveat)}</p></div>`;
}

function activeSector() {
  return $(".sector-tabs button.active")?.dataset.sector || "All";
}

function renderSectorTabs(current = "All") {
  // Derived from the data, not hardcoded. The fixed list offered Healthcare
  // (which no security has, so the tab showed nothing) and omitted
  // Technology, which holds the top-ranked names.
  const tabs = $(".sector-tabs");
  if (!tabs) return;
  const present = [...new Set(rankingData.map((s) => s.sector).filter(Boolean))].sort();
  // A sector the previous run had may be gone from this one; fall back to All
  // rather than leaving no tab selected.
  const selected = present.includes(current) ? current : "All";
  tabs.innerHTML = ["All", ...present].map((name) => {
    const active = name === selected;
    const count = name === "All" ? rankingData.length : rankingData.filter((s) => s.sector === name).length;
    return `<button class="${active ? "active" : ""}" type="button" aria-pressed="${active}" data-sector="${escapeHtml(name)}">${escapeHtml(name)} (${count})</button>`;
  }).join("");
}

function renderRankings(sector = "All") {
  const rows = sector === "All" ? rankingData : rankingData.filter((stock) => stock.sector === sector);
  if (!rows.length) {
    const message = rankingData.length ? `No published rankings for ${sector}.` : "No rankings have been published yet.";
    rankingRows.innerHTML = `<tr><td class="empty-table" colspan="5">${escapeHtml(message)}</td></tr>`;
    return;
  }
  rankingRows.innerHTML = rows.map((stock) => `
    <tr><td class="rank-number">${String(stock.rank).padStart(2, "0")}</td>
      <td><div class="company-cell"><span class="ticker-avatar">${escapeHtml(stock.ticker.slice(0, 2))}</span><div><strong>${escapeHtml(stock.ticker)}</strong><span>${escapeHtml(stock.name)}</span><small>${escapeHtml(stock.reason || stock.sector)}</small></div></div></td>
      <td><span class="score-pill">${Math.round(stock.score)}</span></td>
      <td><span class="factset-badge ${stock.stage === "factset_validated" ? "validated" : ""}">${stock.fundamentals === null ? escapeHtml(stock.factsetAction || "Summary required") : `Validated · ${Math.round(stock.fundamentals)}`}</span></td>
      <td><button class="row-action" type="button" data-add-ticker="${escapeHtml(stock.ticker)}">Use in portfolio</button>
        <button class="why-toggle" type="button" data-why="${escapeHtml(stock.ticker)}">Why?</button>
        <button class="row-action" type="button" data-ai-stock="${escapeHtml(stock.ticker)}">Explain</button></td></tr>
    <tr class="score-why-row" data-why-row="${escapeHtml(stock.ticker)}" hidden><td colspan="5">${scoreBreakdown(stock)}</td></tr>
  `).join("");
}

function setRankingEntries(entries) {
  rankingData = entries.map((entry) => ({
    rank: entry.rank, ticker: entry.ticker, name: entry.company_name || entry.ticker,
    sector: entry.sector || "Unclassified", score: score(entry.composite_score),
    momentum: score(entry.momentum_score), fundamentals: score(entry.fundamental_score),
    scoreDate: entry.score_date, methodologyVersion: entry.methodology_version,
    stage: entry.components?.ranking_stage || "published",
    reason: entry.components?.selection_reason || "",
    factsetAction: entry.components?.factset_action || "",
    components: entry.components || {},
  }));
  renderSectorTabs(activeSector());
}

async function loadRankings() {
  $("#ranking-status").textContent = "Loading rankings…";
  $("#ranking-count").textContent = "";
  $("#retry-rankings").hidden = true;
  rankingRows.innerHTML = '<tr><td class="empty-table" colspan="5">Loading rankings…</td></tr>';
  try {
    const entries = await window.SignalistApi.getRankings();
    setRankingEntries(entries);
    const latest = rankingData[0];
    const validated = latest?.stage === "factset_validated";
    $("#ranking-version").textContent = latest ? `Step 1 · ${validated ? "FactSet validated" : "Initial ranking"}` : "Step 1 · Recommendations";
    $("#ranking-status").textContent = latest
      ? `${validated ? "Financial-quality review" : "Market ranking"} published ${formatDate(latest.scoreDate)}. ${validated ? "These companies passed the FactSet gate." : "Upload the complete FactSet Summary package below for the deeper quality screen."}`
      : "No recommendation run has been published yet.";
    $("#ranking-count").textContent = `${rankingData.length} ${rankingData.length === 1 ? "result" : "results"}`;
    renderRankings(activeSector());
  } catch (error) {
    rankingData = [];
    $("#ranking-version").textContent = "Step 1 · Recommendations unavailable";
    $("#ranking-status").textContent = error.message;
    rankingRows.innerHTML = '<tr><td class="empty-table" colspan="5">Recommendations could not be loaded. Your portfolio and backtests are still available.</td></tr>';
    $("#retry-rankings").hidden = false;
  }
}

async function importFactSet(event) {
  event.preventDefault();
  const files = $("#factset-files").files;
  const status = $("#factset-status");
  const button = $("#run-factset");
  if (!files.length) {
    status.textContent = "Choose one or more FactSet Summary CSV files.";
    return;
  }
  button.disabled = true;
  button.textContent = "Validating…";
  status.textContent = "Checking the complete package against the current recommendation queue…";
  try {
    const result = await window.SignalistApi.importFactSet(session.token, files);
    if (!result.success) {
      const messages = result.validation_messages.slice(0, 3).map((item) => item.message || item.code).filter(Boolean);
      status.textContent = `Review incomplete: ${messages.join(" · ") || `${result.error_count} validation errors.`}`;
      return;
    }
    setRankingEntries(result.rankings);
    renderRankings(activeSector());
    $("#ranking-version").textContent = "Step 1 · FactSet validated";
    $("#ranking-status").textContent = `${result.imported_count} companies passed the financial-quality review.`;
    status.textContent = "FactSet review complete. The recommendations above now show the validated set.";
  } catch (error) {
    if (!expireSession(error)) status.textContent = error.message;
  } finally {
    button.disabled = false;
    button.textContent = "Run FactSet review";
  }
}

function renderSelectedStocks() {
  $("#selected-stocks").innerHTML = selectedStocks.length
    ? selectedStocks.map((ticker) => `<button type="button" data-remove-ticker="${escapeHtml(ticker)}" aria-label="Remove ${escapeHtml(ticker)}">${escapeHtml(ticker)} <span>×</span></button>`).join("")
    : "<p>No ticker symbols added.</p>";
  $("#save-portfolio").disabled = selectedStocks.length === 0;
}

function openStockDialog(seedTicker = "") {
  selectedStocks = currentPortfolio ? currentPortfolio.holdings.map((holding) => holding.ticker) : [];
  if (seedTicker && !selectedStocks.includes(seedTicker)) selectedStocks.push(seedTicker);
  $("#portfolio-name").value = currentPortfolio?.name || "";
  $("#stock-dialog-title").textContent = currentPortfolio ? "Edit portfolio" : "Create portfolio";
  $("#stock-dialog-description").textContent = currentPortfolio
    ? "Saving replaces the current holdings and resets them to equal weights."
    : "Add ticker symbols. Holdings will be saved at equal weights.";
  $("#save-portfolio").textContent = currentPortfolio ? "Save changes" : "Save portfolio";
  setError($("#portfolio-error"));
  renderSelectedStocks();
  stockDialog.showModal();
  ($("#portfolio-name").value ? $("#stock-ticker") : $("#portfolio-name")).focus();
}

async function savePortfolio() {
  const name = $("#portfolio-name").value.trim();
  const error = $("#portfolio-error");
  const button = $("#save-portfolio");
  setError(error);
  if (!name || !selectedStocks.length) {
    setError(error, "Enter a portfolio name and add at least one ticker symbol.");
    return;
  }
  button.disabled = true;
  button.textContent = "Saving…";
  const editing = Boolean(currentPortfolio);
  try {
    const portfolio = editing
      ? await window.SignalistApi.updatePortfolio(session.token, currentPortfolio.id, name, selectedStocks)
      : await window.SignalistApi.createPortfolio(session.token, name, selectedStocks);
    const index = portfolios.findIndex((item) => item.id === portfolio.id);
    if (index >= 0) portfolios[index] = portfolio;
    else portfolios.unshift(portfolio);
    renderPortfolio(portfolio);
    setProgression(true);
    stockDialog.close();
    showToast(editing ? "Portfolio updated." : "Portfolio created. Backtests are now unlocked.");
  } catch (requestError) {
    if (!expireSession(requestError)) setError(error, requestError.message);
  } finally {
    button.textContent = editing ? "Save changes" : "Save portfolio";
    button.disabled = selectedStocks.length === 0;
  }
}

function renderGuide() {
  const slide = guideSlides[guideIndex];
  $("#guide-illustration").textContent = slide.number;
  $("#guide-step").textContent = `${guideIndex + 1} of ${guideSlides.length}`;
  $("#guide-title").textContent = slide.title;
  $("#guide-copy").textContent = slide.copy;
  $("#guide-back").hidden = guideIndex === 0;
  $("#guide-next").textContent = guideIndex === guideSlides.length - 1
    ? (currentPortfolio ? "View portfolio" : "Create portfolio")
    : "Next";
  $("#guide-dots").innerHTML = guideSlides.map((_, index) => `<span class="${index === guideIndex ? "active" : ""}"></span>`).join("");
}

function openGuide(force = false) {
  if (!force && window.SignalistOnboarding.isDismissed(session?.email)) return;
  guideIndex = 0;
  renderGuide();
  if (!guideDialog.open) guideDialog.showModal();
}

function maybeOpenGuide() {
  openGuide(false);
}

function dismissGuide() {
  window.SignalistOnboarding.dismiss(session?.email);
  if (guideDialog.open) guideDialog.close();
}

function populateBacktestPortfolios() {
  const select = $("#backtest-portfolio");
  const selected = select.value;
  select.innerHTML = portfolios.map((portfolio) => `<option value="${portfolio.id}">${escapeHtml(portfolio.name)}</option>`).join("");
  if (selected && portfolios.some((portfolio) => String(portfolio.id) === selected)) select.value = selected;
  else if (currentPortfolio) select.value = String(currentPortfolio.id);
}

function setBacktestDefaults() {
  if ($("#backtest-end").value) return;
  const end = new Date();
  const start = new Date(end);
  start.setFullYear(start.getFullYear() - 5);
  $("#backtest-start").value = inputDate(start);
  $("#backtest-end").value = inputDate(end);
}

function backtestPayload() {
  return {
    portfolio_id: Number($("#backtest-portfolio").value),
    benchmark: $("#backtest-benchmark").value.trim().toUpperCase(),
    start_date: $("#backtest-start").value,
    end_date: $("#backtest-end").value,
    rebalance_frequency: $("#backtest-frequency").value,
    initial_capital: Number($("#backtest-capital").value),
    cost_bps: Number($("#backtest-cost").value),
  };
}

function savedBacktestInputKey(backtestId) {
  return `signalist.backtest.v1:${String(session?.email || "account").toLowerCase()}:${backtestId}`;
}

function saveBacktestInputs(backtestId, payload) {
  localStorage.setItem(savedBacktestInputKey(backtestId), JSON.stringify(payload));
}

function loadBacktestInputs(backtestId) {
  try {
    return JSON.parse(localStorage.getItem(savedBacktestInputKey(backtestId))) || {};
  } catch {
    return {};
  }
}

function validateBacktest(payload) {
  if (!payload.portfolio_id) return "Select a portfolio.";
  if (!/^[A-Z][A-Z0-9.-]{0,15}$/.test(payload.benchmark)) return "Enter a valid benchmark ticker, such as SPY.";
  if (!payload.start_date || !payload.end_date) return "Choose a start and end date.";
  if (payload.end_date < payload.start_date) return "End date cannot be before start date.";
  if (!(payload.initial_capital > 0)) return "Starting investment must be greater than $0.";
  if (!(payload.cost_bps >= 0)) return "Trading cost cannot be negative.";
  return "";
}

function renderBacktestHistory(runs) {
  const container = $("#backtest-history");
  if (!runs.length) {
    container.innerHTML = '<p class="muted">No backtests yet.</p>';
    return;
  }
  container.innerHTML = runs.map((run) => `
    <button class="history-item ${run.id === selectedBacktestId ? "active" : ""}" type="button" data-backtest-id="${run.id}">
      <span><strong>${escapeHtml(run.benchmark)}</strong><small>${formatDate(run.start_date)} – ${formatDate(run.end_date)}</small></span>
      <em class="run-status run-status--${escapeHtml(run.status)}">${escapeHtml(run.status)}</em>
    </button>`).join("");
}

async function loadBacktests() {
  try {
    const runs = await window.SignalistApi.listBacktests(session.token);
    renderBacktestHistory(runs);
    if (!selectedBacktestId && runs[0]) selectBacktest(runs[0].id);
    return runs;
  } catch (error) {
    if (expireSession(error)) return [];
    $("#backtest-history").innerHTML = `<p class="form-error">${escapeHtml(error.message)}</p>`;
    return [];
  }
}

function metricValue(value, percent = false) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
  return percent ? `${(Number(value) * 100).toFixed(2)}%` : Number(value).toFixed(2);
}

function renderDrawdown(points) {
  const valid = (points || []).filter((p) => Number.isFinite(p.drawdown));
  const card = $("#drawdown-card");
  if (valid.length < 2) { card.hidden = true; return; }

  const values = valid.map((p) => p.drawdown);
  const worst = Math.min(...values);
  // Always anchor the top of the chart at 0: a drawdown plot that rescales
  // its zero line makes a mild dip look like a crash.
  const path = pathFor(values, worst, 0, 20, 200);
  // Close the area UP to the zero line, not down to the floor: the shading is
  // the water the portfolio is under, so the deepest day must be the most
  // shaded. Closing downward inverts that and makes a crash look like a peak.
  $("#drawdown-path").setAttribute("d", `${path} L 780 20 L 54 20 Z`);

  const trough = valid[values.indexOf(worst)];
  // Two labels only: 0% at the surface and the worst reading at the floor.
  // A drawdown axis has no other number worth the ink.
  $("#drawdown-axis").innerHTML =
    `<text x="48" y="24" text-anchor="end">0%</text>` +
    `<text x="48" y="204" text-anchor="end">${escapeHtml(metricValue(worst, true))}</text>`;

  $("#drawdown-caption").textContent =
    `Deepest drawdown ${metricValue(worst, true)} on ${formatDate(trough.trading_date)}, across ${valid.length} sessions.`;
  $("#drawdown-chart").setAttribute("aria-label",
    `Drawdown curve, deepest ${metricValue(worst, true)} on ${formatDate(trough.trading_date)}`);
  card.hidden = false;
}

function renderExecution(metrics) {
  const panel = $("#execution-panel");
  const rows = [
    ["Turnover", Number.isFinite(metrics?.total_turnover) ? metricValue(metrics.total_turnover, true) : null],
    ["Transaction costs", Number.isFinite(metrics?.transaction_costs)
      ? metrics.transaction_costs.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 }) : null],
    ["Average cash weight", Number.isFinite(metrics?.average_cash_weight) ? metricValue(metrics.average_cash_weight, true) : null],
    ["Rebalances", Number.isFinite(metrics?.rebalance_count) ? String(metrics.rebalance_count) : null],
  ].filter(([, value]) => value !== null);

  if (!rows.length) { panel.hidden = true; return; }
  $("#execution-stats").innerHTML = rows
    .map(([label, value]) => `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`).join("");
  panel.hidden = false;
}

function renderMetrics(metrics) {
  const definitions = [
    ["Total return", "total_return", true, false], ["Annualized return", "annualized_return", true, false],
    ["Volatility", "volatility", true, false], ["Sharpe ratio", "sharpe_ratio", false, false],
    ["Max drawdown", "max_drawdown", true, false], ["Alpha vs. benchmark", "alpha_vs_benchmark", true, false],
    // Tail risk: what a bad day costs, and what the bad days average once
    // you are already in one. Beta says how much of it is just the market.
    ["Daily VaR (95%)", "var_95", true, true],
    ["Daily CVaR (95%)", "cvar_95", true, true],
    ["Beta vs. benchmark", "beta_vs_benchmark", false, true],
  ];
  $("#backtest-metrics").innerHTML = definitions.map(([label, key, percent, risky]) => (
    `<article${risky ? ' class="risk-tile"' : ""}><span>${label}</span><strong>${metricValue(metrics?.[key], percent)}</strong></article>`
  )).join("");
  $("#backtest-metrics").hidden = false;
}

function pathFor(values, min, max, top = 28, bottom = 272) {
  const left = 54, right = 780;
  const span = max - min || 1;
  return values.map((value, index) => {
    const x = left + (index / Math.max(1, values.length - 1)) * (right - left);
    const y = bottom - ((value - min) / span) * (bottom - top);
    return `${index ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`;
  }).join(" ");
}

function renderEquityCurve(points) {
  const valid = (points || []).filter((point) => Number.isFinite(Number(point.portfolio_value)) && Number.isFinite(Number(point.benchmark_value)));
  if (valid.length < 2) {
    $("#equity-card").hidden = true;
    return;
  }
  const portfolioValues = valid.map((point) => Number(point.portfolio_value));
  const benchmarkValues = valid.map((point) => Number(point.benchmark_value));
  const all = [...portfolioValues, ...benchmarkValues];
  const min = Math.min(...all), max = Math.max(...all);
  $("#portfolio-path").setAttribute("d", pathFor(portfolioValues, min, max));
  $("#benchmark-path").setAttribute("d", pathFor(benchmarkValues, min, max));
  $("#equity-chart").setAttribute("aria-label", `Portfolio and ${valid.length} benchmark equity points from ${formatDate(valid[0].trading_date)} to ${formatDate(valid.at(-1).trading_date)}`);
  $("#equity-card").hidden = false;
}

function validationMessage(item) {
  if (typeof item === "string") return item;
  return item?.message || item?.detail || item?.reason || JSON.stringify(item);
}

function renderValidation(messages) {
  const values = (messages || []).map(validationMessage).filter(Boolean);
  $("#validation-list").innerHTML = values.map((message) => `<li>${escapeHtml(message)}</li>`).join("");
  $("#backtest-validation").hidden = values.length === 0;
}

function renderRunParameters(detail) {
  // The API is the source of truth: it records what the run actually used.
  // loadBacktestInputs only remembers what *this browser* submitted, so it is
  // empty for a run opened in a new session or on another device.
  const submitted = loadBacktestInputs(detail.id);
  const capital = Number.isFinite(detail.initial_capital) ? detail.initial_capital : submitted.initial_capital;
  const cost = Number.isFinite(detail.cost_bps) ? detail.cost_bps : submitted.cost_bps;
  const portfolio = portfolios.find((item) => item.id === detail.portfolio_id);
  const values = [
    ["Portfolio", portfolio?.name || `Portfolio ${detail.portfolio_id || "—"}`],
    ["Benchmark", detail.benchmark],
    ["Date range", `${formatDate(detail.start_date)} – ${formatDate(detail.end_date)}`],
    ["Rebalancing", detail.rebalance_frequency],
    ["Starting investment", capital ? Number(capital).toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }) : "Not returned by API"],
    ["Trading cost", Number.isFinite(cost) ? `${cost} bps` : "Not returned by API"],
  ];
  $("#backtest-parameters").innerHTML = values.map(([label, value]) => `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`).join("");
}

function renderBacktestDetail(detail) {
  selectedBacktestDetail = detail;
  $("#backtest-empty").hidden = true;
  $("#backtest-detail").hidden = false;
  $("#backtest-status").textContent = detail.status;
  $("#backtest-status").className = `status-badge status-badge--${detail.status}`;
  $("#backtest-result-title").textContent = `${detail.benchmark} backtest`;
  $("#backtest-result-summary").textContent = `${formatDate(detail.start_date)} – ${formatDate(detail.end_date)} · ${detail.rebalance_frequency} rebalancing`;
  $("#edit-backtest").textContent = detail.status === "failed" ? "Edit and run again" : "Edit inputs";
  $("#backtest-metrics").hidden = true;
  $("#equity-card").hidden = true;

  const messages = {
    queued: "Waiting for the backtest worker. Results will appear here when processing begins.",
    running: "The worker is processing this backtest. This page will update automatically.",
    failed: "The worker could not complete this run. Review the notes below, adjust the inputs, and try again.",
    completed: detail.completed_at ? `Completed ${formatDate(detail.completed_at)}.` : "Backtest completed.",
  };
  $("#backtest-job-message").textContent = messages[detail.status] || `Status: ${detail.status}`;
  renderRunParameters(detail);
  renderValidation(detail.validation_messages);

  if (detail.status === "completed") {
    renderMetrics(detail.metrics);
    renderEquityCurve(detail.equity_curve);
    renderDrawdown(detail.equity_curve);
    renderExecution(detail.metrics);
  } else {
    $("#drawdown-card").hidden = true;
    $("#execution-panel").hidden = true;
  }
}

function renderAIReport(report) {
  const provider = report.provider ? ` (${report.provider}, ${report.data_policy || "default policy"})` : "";
  $("#ai-status").textContent = report.status === "completed" ? `Generated from your stored ranking and backtest data${provider}.` : report.error || `Analysis ${report.status}${provider}…`;
  if (report.status !== "completed" || !report.report) return;
  const body = report.report;
  const citations = (report.citations || []).map((citation) => `<li><strong>${escapeHtml(citation.source_key)}</strong>: ${escapeHtml(citation.excerpt)}</li>`).join("");
  $("#ai-report").innerHTML = `<h3>${escapeHtml(body.title || "Analysis")}</h3><p>${escapeHtml(body.summary || "")}</p>${(body.sections || []).map((section) => `<section><h4>${escapeHtml(section.title)}</h4><p>${escapeHtml(section.body)}</p></section>`).join("")}<p><strong>Limitations</strong>: ${escapeHtml((body.limitations || []).join(" "))}</p>${citations ? `<h4>Sources</h4><ul>${citations}</ul>` : ""}`;
  $("#ai-report").hidden = false;
  $("#ai-chat-form").hidden = false;
}

async function openAIReport(payload) {
  if (!session?.token) return;
  clearTimeout(aiPollTimer);
  $("#ai-dialog").showModal();
  $("#ai-report").hidden = true;
  $("#ai-chat-form").hidden = true;
  $("#ai-status").textContent = "Queueing analysis…";
  try {
    const created = await window.SignalistApi.createAIReport(session.token, payload);
    aiReportId = created.id;
    renderAIReport(created);
    const poll = async () => {
      const report = await window.SignalistApi.getAIReport(session.token, aiReportId);
      renderAIReport(report);
      if (!["completed", "failed"].includes(report.status)) aiPollTimer = setTimeout(poll, 3000);
      else if (report.status === "completed") loadAIMessages();
    };
    aiPollTimer = setTimeout(poll, 1200);
  } catch (error) {
    $("#ai-status").textContent = error.message;
  }
}

async function loadAIMessages() {
  if (!aiReportId) return;
  const messages = await window.SignalistApi.getAIMessages(session.token, aiReportId);
  $("#ai-messages").innerHTML = messages.map((message) => `<p><strong>${message.role === "assistant" ? "Assistant" : "You"}:</strong> ${escapeHtml(message.content)}</p>`).join("");
  return messages;
}

function scheduleBacktestPoll(detail) {
  stopBacktestPolling();
  if (!["queued", "running"].includes(detail.status) || document.hidden) return;
  backtestPollTimer = setTimeout(() => refreshBacktestDetail(detail.id), 3000);
}

async function refreshBacktestDetail(backtestId) {
  try {
    const detail = await window.SignalistApi.getBacktest(session.token, backtestId);
    if (selectedBacktestId !== backtestId) return;
    renderBacktestDetail(detail);
    scheduleBacktestPoll(detail);
    if (["completed", "failed"].includes(detail.status)) loadBacktests();
  } catch (error) {
    if (expireSession(error)) return;
    $("#backtest-job-message").textContent = `Could not refresh this run: ${error.message}`;
  }
}

function selectBacktest(backtestId) {
  selectedBacktestId = Number(backtestId);
  $("#backtest-empty").hidden = true;
  $("#backtest-detail").hidden = false;
  $("#backtest-job-message").textContent = "Loading backtest…";
  refreshBacktestDetail(selectedBacktestId);
  loadBacktests();
}

async function submitBacktest(event) {
  event.preventDefault();
  const payload = backtestPayload();
  const validation = validateBacktest(payload);
  setError($("#backtest-form-error"), validation);
  if (validation) return;
  const button = $("#run-backtest");
  button.disabled = true;
  button.textContent = "Queueing…";
  try {
    const created = await window.SignalistApi.createBacktest(session.token, payload);
    saveBacktestInputs(created.id, payload);
    selectedBacktestId = created.id;
    renderBacktestDetail({ ...created, metrics: null, equity_curve: [], validation_messages: [] });
    scheduleBacktestPoll(created);
    await loadBacktests();
    showToast("Backtest queued. The results panel will update automatically.");
  } catch (error) {
    if (!expireSession(error)) setError($("#backtest-form-error"), error.message);
  } finally {
    button.disabled = false;
    button.textContent = "Run backtest";
  }
}

$("#current-date").textContent = new Intl.DateTimeFormat("en-US", { weekday: "long", month: "long", day: "numeric" }).format(new Date());
$("#retry-google").addEventListener("click", mountGoogleSignIn);
$("#profile-button").addEventListener("click", () => {
  const menu = $("#profile-menu");
  menu.hidden = !menu.hidden;
  $("#profile-button").setAttribute("aria-expanded", String(!menu.hidden));
});
$("#logout-button").addEventListener("click", () => {
  window.SignalistGoogleAuth.disableAutoSelect();
  window.SignalistSession.clear();
  session = null;
  portfolios = [];
  currentPortfolio = null;
  $("#profile-menu").hidden = true;
  showAuth();
});

$("#create-portfolio").addEventListener("click", () => openStockDialog());
$("#locked-create-portfolio").addEventListener("click", () => openStockDialog());
$("#edit-portfolio").addEventListener("click", () => openStockDialog());
$("#retry-portfolio").addEventListener("click", loadPortfolios);
$("#retry-rankings").addEventListener("click", loadRankings);
$("#factset-form").addEventListener("submit", importFactSet);

$(".sector-tabs").addEventListener("click", (event) => {
  // Delegated, because renderSectorTabs rebuilds these buttons from the data.
  const button = event.target.closest("button[data-sector]");
  if (!button) return;
  document.querySelectorAll(".sector-tabs button").forEach((item) => {
    const active = item === button;
    item.classList.toggle("active", active);
    item.setAttribute("aria-pressed", String(active));
  });
  renderRankings(button.dataset.sector);
});

rankingRows.addEventListener("click", (event) => {
  const why = event.target.closest("[data-why]");
  if (why) {
    const row = rankingRows.querySelector(`[data-why-row="${CSS.escape(why.dataset.why)}"]`);
    if (row) {
      row.hidden = !row.hidden;
      why.textContent = row.hidden ? "Why?" : "Hide";
    }
    return;
  }

  const button = event.target.closest("[data-add-ticker]");
  if (!button) return;
  if (currentPortfolio?.holdings.some((holding) => holding.ticker === button.dataset.addTicker)) {
    showToast(`${button.dataset.addTicker} is already in your portfolio.`);
    return;
  }
  openStockDialog(button.dataset.addTicker);
});

$("#stock-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const input = $("#stock-ticker");
  const ticker = input.value.trim().toUpperCase();
  const error = $("#portfolio-error");
  if (!/^[A-Z][A-Z0-9.-]{0,15}$/.test(ticker)) {
    setError(error, "Enter a valid ticker symbol, such as AAPL.");
    return;
  }
  if (selectedStocks.includes(ticker)) {
    setError(error, `${ticker} is already in this portfolio.`);
    return;
  }
  setError(error);
  selectedStocks.push(ticker);
  input.value = "";
  renderSelectedStocks();
  input.focus();
});

$("#selected-stocks").addEventListener("click", (event) => {
  const button = event.target.closest("[data-remove-ticker]");
  if (!button) return;
  selectedStocks = selectedStocks.filter((ticker) => ticker !== button.dataset.removeTicker);
  renderSelectedStocks();
});
$("#save-portfolio").addEventListener("click", savePortfolio);

$("#open-guide").addEventListener("click", () => openGuide(true));
$("#guide-skip").addEventListener("click", dismissGuide);
$("#guide-back").addEventListener("click", () => { guideIndex = Math.max(0, guideIndex - 1); renderGuide(); });
$("#guide-next").addEventListener("click", () => {
  if (guideIndex < guideSlides.length - 1) {
    guideIndex += 1;
    renderGuide();
    return;
  }
  dismissGuide();
  if (currentPortfolio) $("#portfolio").scrollIntoView({ behavior: "smooth", block: "start" });
  else openStockDialog();
});

$("#backtest-form").addEventListener("submit", submitBacktest);
$("#refresh-backtests").addEventListener("click", loadBacktests);
$("#backtest-history").addEventListener("click", (event) => {
  const button = event.target.closest("[data-backtest-id]");
  if (button) selectBacktest(button.dataset.backtestId);
});
rankingRows.addEventListener("click", (event) => {
  const explain = event.target.closest("[data-ai-stock]");
  if (explain) openAIReport({ kind: "stock", ticker: explain.dataset.aiStock });
});
$("#analyze-backtest").addEventListener("click", () => {
  if (selectedBacktestId) openAIReport({ kind: "backtest", backtest_id: selectedBacktestId });
});
$("#analyze-recommendations").addEventListener("click", () => openAIReport({ kind: "recommendations" }));
$("#ai-chat-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = $("#ai-chat-input");
  if (!input.value.trim() || !aiReportId) return;
  const content = input.value.trim();
  input.value = "";
  await window.SignalistApi.sendAIMessage(session.token, aiReportId, content);
  $("#ai-status").textContent = "The analysis model is answering…";
  const pollMessages = async (attempt = 0) => {
    const messages = await loadAIMessages();
    const pending = messages?.some((message) => message.role === "user" && message.status === "queued");
    if (pending && attempt < 20) setTimeout(() => pollMessages(attempt + 1), 1500);
    else $("#ai-status").textContent = pending ? "The analysis worker is still busy." : "Analysis updated.";
  };
  pollMessages();
});
$("#edit-backtest").addEventListener("click", () => {
  if (selectedBacktestDetail) {
    const submitted = loadBacktestInputs(selectedBacktestDetail.id);
    $("#backtest-portfolio").value = String(selectedBacktestDetail.portfolio_id || submitted.portfolio_id || "");
    $("#backtest-benchmark").value = selectedBacktestDetail.benchmark || submitted.benchmark || "SPY";
    $("#backtest-start").value = selectedBacktestDetail.start_date || submitted.start_date || "";
    $("#backtest-end").value = selectedBacktestDetail.end_date || submitted.end_date || "";
    $("#backtest-frequency").value = selectedBacktestDetail.rebalance_frequency || submitted.rebalance_frequency || "monthly";
    if (submitted.initial_capital) $("#backtest-capital").value = submitted.initial_capital;
    if (Number.isFinite(submitted.cost_bps)) $("#backtest-cost").value = submitted.cost_bps;
  }
  $("#backtest-form").scrollIntoView({ behavior: "smooth", block: "center" });
});
document.addEventListener("visibilitychange", () => {
  if (document.hidden) stopBacktestPolling();
  else if (selectedBacktestId && session) refreshBacktestDetail(selectedBacktestId);
});

renderSelectedStocks();
setBacktestDefaults();
if (session?.token) showApp();
else showAuth();
