// Resolved at page load, not baked in at build time — there is no build step.
//
// In production Caddy serves these static files and proxies /api/* to the
// backend on the same origin, so an empty base URL is correct and no CORS
// grant is needed. Local development is the exception: `npm start` serves
// the files on :4173 while the API runs separately on :8081.
window.SignalistConfig = {
  apiBaseUrl: window.location.port === "4173" ? "http://localhost:8081" : "",
};
