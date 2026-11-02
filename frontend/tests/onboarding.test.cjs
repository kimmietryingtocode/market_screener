const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");
const vm = require("node:vm");

function loadOnboarding() {
  const values = new Map();
  const context = {
    localStorage: {
      getItem: (key) => values.get(key) ?? null,
      setItem: (key, value) => values.set(key, value),
    },
    window: {},
  };
  vm.runInNewContext(fs.readFileSync("onboarding.js", "utf8"), context);
  return context.window.SignalistOnboarding;
}

test("dismissal is remembered once per normalized account", () => {
  const onboarding = loadOnboarding();

  onboarding.dismiss(" Person@Example.com ");

  assert.equal(onboarding.isDismissed("person@example.com"), true);
  assert.equal(onboarding.isDismissed("someone@example.com"), false);
});
