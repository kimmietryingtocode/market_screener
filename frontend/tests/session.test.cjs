const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");
const vm = require("node:vm");

function loadSessionModule() {
  const values = new Map();
  const context = {
    sessionStorage: {
      getItem: (key) => values.get(key) ?? null,
      removeItem: (key) => values.delete(key),
      setItem: (key, value) => values.set(key, value),
    },
    window: {},
  };
  vm.runInNewContext(fs.readFileSync("session.js", "utf8"), context);
  return context.window.SignalistSession;
}

test("authenticated sessions retain only the app token and profile", () => {
  const session = loadSessionModule();

  session.save({ token: "app-token", email: "person@example.com", display_name: "Person" });

  assert.deepEqual({ ...session.load() }, {
    token: "app-token",
    email: "person@example.com",
    displayName: "Person",
  });
});

test("clearing a session signs the browser out", () => {
  const session = loadSessionModule();
  session.save({ token: "app-token", email: "person@example.com" });

  session.clear();

  assert.equal(session.load(), null);
});
