(function exposeGoogleAuth() {
  const scriptUrl = "https://accounts.google.com/gsi/client";
  let libraryPromise;
  let initialized = false;

  function loadLibrary() {
    if (window.google?.accounts?.id) return Promise.resolve(window.google.accounts.id);
    if (libraryPromise) return libraryPromise;

    libraryPromise = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = scriptUrl;
      script.async = true;
      script.onload = () => window.google?.accounts?.id
        ? resolve(window.google.accounts.id)
        : reject(new Error("Google sign-in loaded without its account API."));
      script.onerror = () => reject(new Error("Google sign-in could not be loaded. Check your connection and try again."));
      document.head.appendChild(script);
    }).catch((error) => {
      libraryPromise = null;
      throw error;
    });
    return libraryPromise;
  }

  async function mount({ clientId, container, onCredential, onClick }) {
    const identity = await loadLibrary();
    if (!initialized) {
      identity.initialize({
        client_id: clientId,
        callback: onCredential,
        auto_select: false,
        button_auto_select: false,
        use_fedcm_for_button: true,
      });
      initialized = true;
    }
    container.replaceChildren();
    const availableWidth = Math.floor(container.getBoundingClientRect?.().width || 320);
    identity.renderButton(container, {
      type: "standard",
      theme: "outline",
      size: "large",
      shape: "pill",
      text: "continue_with",
      width: Math.min(360, Math.max(240, availableWidth)),
      click_listener: onClick,
    });
  }

  function disableAutoSelect() {
    window.google?.accounts?.id?.disableAutoSelect();
  }

  window.SignalistGoogleAuth = { disableAutoSelect, loadLibrary, mount };
})();
