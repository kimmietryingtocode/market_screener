(function exposeSession() {
  const storageKey = "signalist.session";

  function load() {
    try {
      const value = JSON.parse(sessionStorage.getItem(storageKey));
      return value && typeof value === "object" ? value : null;
    } catch {
      sessionStorage.removeItem(storageKey);
      return null;
    }
  }

  function save(auth, fallbackEmail = "") {
    const session = {
      token: auth.token || null,
      email: auth.email || fallbackEmail,
      displayName: auth.display_name || "",
    };
    sessionStorage.setItem(storageKey, JSON.stringify(session));
    return session;
  }

  function clear() {
    sessionStorage.removeItem(storageKey);
  }

  window.SignalistSession = { clear, load, save };
})();
