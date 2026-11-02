(function exposeApiClient() {
  const apiBaseUrl = window.SignalistConfig.apiBaseUrl.replace(/\/$/, "");

  async function request(path, { method = "GET", token, body, form } = {}) {
    const response = await fetch(`${apiBaseUrl}${path}`, {
      method,
      headers: {
        Accept: "application/json",
        ...(body ? { "Content-Type": "application/json" } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      ...(body ? { body: JSON.stringify(body) } : {}),
      ...(form ? { body: form } : {}),
    });

    if (!response.ok) {
      const payload = await response.json().catch(() => null);
      const detail = Array.isArray(payload?.detail)
        ? payload.detail.map((item) => item.msg).join(", ")
        : payload?.detail;
      const error = new Error(detail || `Request failed (${response.status})`);
      error.status = response.status;
      throw error;
    }

    return response.status === 204 ? null : response.json();
  }

  async function getRankings(limit = 50) {
    return request(`/api/v1/rankings?limit=${limit}`);
  }

  function importFactSet(token, files) {
    const form = new FormData();
    Array.from(files).forEach((file) => form.append("files", file));
    return request("/api/v1/rankings/factset", { method: "POST", token, form });
  }

  function googleLogin(credential) {
    return request("/api/v1/auth/google", { method: "POST", body: { credential } });
  }

  function getGoogleConfig() {
    return request("/api/v1/auth/google/config");
  }

  function listPortfolios(token) {
    return request("/api/v1/portfolios", { token });
  }

  function portfolioBody(name, tickers) {
    const weight = 1 / tickers.length;
    const holdings = tickers.map((ticker, index) => ({
      ticker,
      weight: index === tickers.length - 1 ? 1 - weight * (tickers.length - 1) : weight,
    }));
    return { name, holdings };
  }

  function createPortfolio(token, name, tickers) {
    return request("/api/v1/portfolios", {
      method: "POST",
      token,
      body: portfolioBody(name, tickers),
    });
  }

  function updatePortfolio(token, portfolioId, name, tickers) {
    return request(`/api/v1/portfolios/${portfolioId}`, {
      method: "PUT",
      token,
      body: portfolioBody(name, tickers),
    });
  }

  function createBacktest(token, payload) {
    return request("/api/v1/backtests", { method: "POST", token, body: payload });
  }

  function listBacktests(token) {
    return request("/api/v1/backtests", { token });
  }

  function getBacktest(token, backtestId) {
    return request(`/api/v1/backtests/${backtestId}`, { token });
  }

  window.SignalistApi = {
    createBacktest,
    createPortfolio,
    getBacktest,
    getGoogleConfig,
    getRankings,
    googleLogin,
    importFactSet,
    listBacktests,
    listPortfolios,
    updatePortfolio,
  };
})();
