(function exposeOnboarding() {
  const prefix = "signalist.onboarding.v1:";

  function key(email) {
    return `${prefix}${String(email || "account").trim().toLowerCase()}`;
  }

  function isDismissed(email) {
    return localStorage.getItem(key(email)) === "done";
  }

  function dismiss(email) {
    localStorage.setItem(key(email), "done");
  }

  window.SignalistOnboarding = { dismiss, isDismissed, key };
})();
