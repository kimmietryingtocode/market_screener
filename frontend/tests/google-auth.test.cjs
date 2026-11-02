const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");
const vm = require("node:vm");

function loadGoogleAuth({ failFirstLoad = false } = {}) {
  const calls = { initialize: [], render: [], disable: 0, scripts: [] };
  const identity = {
    initialize: (options) => calls.initialize.push(options),
    renderButton: (container, options) => calls.render.push({ container, options }),
    disableAutoSelect: () => { calls.disable += 1; },
  };
  let attempts = 0;
  const context = {
    window: {},
    document: {
      createElement: () => ({}),
      head: {
        appendChild: (script) => {
          attempts += 1;
          calls.scripts.push(script);
          queueMicrotask(() => {
            if (failFirstLoad && attempts === 1) script.onerror();
            else {
              context.window.google = { accounts: { id: identity } };
              script.onload();
            }
          });
        },
      },
    },
    Error,
    Promise,
    queueMicrotask,
  };
  vm.runInNewContext(fs.readFileSync("google-auth.js", "utf8"), context);
  return { auth: context.window.SignalistGoogleAuth, calls };
}

function container() {
  return { replaceChildren() {} };
}

test("GIS loads deterministically, initializes once, and renders its official button", async () => {
  const { auth, calls } = loadGoogleAuth();
  const onCredential = () => {};

  await auth.mount({ clientId: "client-id", container: container(), onCredential, onClick: () => {} });
  await auth.mount({ clientId: "client-id", container: container(), onCredential, onClick: () => {} });

  assert.equal(calls.scripts.length, 1);
  assert.equal(calls.scripts[0].src, "https://accounts.google.com/gsi/client");
  assert.equal(calls.initialize.length, 1);
  assert.equal(calls.initialize[0].callback, onCredential);
  assert.equal(calls.initialize[0].use_fedcm_for_button, true);
  assert.equal(calls.initialize[0].auto_select, false);
  assert.equal(calls.initialize[0].button_auto_select, false);
  assert.equal(calls.render.length, 2);
  assert.equal(calls.render[0].options.text, "continue_with");
});

test("a failed GIS script load can be retried", async () => {
  const { auth, calls } = loadGoogleAuth({ failFirstLoad: true });
  const options = { clientId: "client-id", container: container(), onCredential: () => {}, onClick: () => {} };

  await assert.rejects(auth.mount(options), /could not be loaded/i);
  await auth.mount(options);

  assert.equal(calls.scripts.length, 2);
  assert.equal(calls.initialize.length, 1);
});

test("logout protection delegates to GIS disableAutoSelect", async () => {
  const { auth, calls } = loadGoogleAuth();
  await auth.mount({ clientId: "client-id", container: container(), onCredential: () => {}, onClick: () => {} });

  auth.disableAutoSelect();

  assert.equal(calls.disable, 1);
});
