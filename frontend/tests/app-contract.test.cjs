// These two tests exist because of a bug that shipped silently.
//
// renderDrawdown called formatPercent(), which does not exist in this
// codebase (the percent formatter is metricValue(value, true)). The resulting
// ReferenceError was thrown inside renderBacktestDetail, which runs inside
// refreshBacktestDetail's try/catch — so the exception was swallowed, the
// underwater chart and execution panel silently stayed hidden, and every
// existing test still passed. Nothing in the suite executed app.js.
//
// A full DOM harness would be the thorough answer, but it needs a dependency
// this frontend does not have. These two static checks cost nothing and catch
// the two ways that failure actually happens: calling a function that is not
// there, and reaching for an element that is not there.

const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");

const app = fs.readFileSync("app.js", "utf8");
const html = fs.readFileSync("index.html", "utf8");

// Blank out comments and the text inside string and template literals, so
// their contents are never mistaken for code. Written as a scanner rather
// than a regex: template literals nest (`${cond ? `a` : `b`}`), and a regex
// that tries to match them swallows whole functions, which quietly turns this
// test into one that passes because it checked nothing.
//
// Code inside a template's ${...} is kept — it really is code.
// A `/` begins a regex literal rather than a division when the last
// meaningful character before it cannot end an expression. Without this the
// scanner walks into /[&<>"']/ in escapeHtml, mistakes the quote inside the
// character class for the start of a string, and loses sync with the rest of
// the file — which silently empties this whole test.
function startsRegex(out) {
  const before = out.replace(/\s+$/, "");
  if (!before) return true;
  const last = before[before.length - 1];
  if ("(,=:[!&|?{};+-*%~^<>".includes(last)) return true;
  return /\b(return|typeof|case|in|of|new|delete|void|do|else|yield|await)$/.test(before);
}

function stripNonCode(source) {
  let out = "";
  let i = 0;
  const templateStack = [];

  while (i < source.length) {
    const two = source.slice(i, i + 2);

    if (two === "/*") {
      const end = source.indexOf("*/", i + 2);
      const stop = end === -1 ? source.length : end + 2;
      out += source.slice(i, stop).replace(/[^\n]/g, " ");
      i = stop;
      continue;
    }
    if (two === "//") {
      const end = source.indexOf("\n", i);
      const stop = end === -1 ? source.length : end;
      out += " ".repeat(stop - i);
      i = stop;
      continue;
    }

    const ch = source[i];

    if (ch === "/" && startsRegex(out)) {
      out += "/";
      i += 1;
      let inClass = false;
      while (i < source.length) {
        const c = source[i];
        if (c === "\\") { out += "  "; i += 2; continue; }
        if (c === "\n") break; // unterminated: not a regex after all
        if (c === "[") inClass = true;
        else if (c === "]") inClass = false;
        else if (c === "/" && !inClass) { out += "/"; i += 1; break; }
        out += " ";
        i += 1;
      }
      continue;
    }

    if (ch === '"' || ch === "'") {
      out += ch;
      i += 1;
      while (i < source.length && source[i] !== ch) {
        if (source[i] === "\\") { out += "  "; i += 2; continue; }
        out += source[i] === "\n" ? "\n" : " ";
        i += 1;
      }
      out += ch;
      i += 1;
      continue;
    }

    if (ch === "`") {
      templateStack.push({ braces: 0 });
      out += "`";
      i += 1;
      // Text run inside the template, up to ` or ${
      while (i < source.length) {
        if (source[i] === "\\") { out += "  "; i += 2; continue; }
        if (source[i] === "`") { out += "`"; i += 1; templateStack.pop(); break; }
        if (source.slice(i, i + 2) === "${") { out += "${"; i += 2; break; }
        out += source[i] === "\n" ? "\n" : " ";
        i += 1;
      }
      continue;
    }

    // Closing a ${...} returns us to template text.
    if (ch === "}" && templateStack.length) {
      const top = templateStack[templateStack.length - 1];
      if (top.braces === 0) {
        out += "}";
        i += 1;
        while (i < source.length) {
          if (source[i] === "\\") { out += "  "; i += 2; continue; }
          if (source[i] === "`") { out += "`"; i += 1; templateStack.pop(); break; }
          if (source.slice(i, i + 2) === "${") { out += "${"; i += 2; break; }
          out += source[i] === "\n" ? "\n" : " ";
          i += 1;
        }
        continue;
      }
      top.braces -= 1;
    } else if (ch === "{" && templateStack.length) {
      templateStack[templateStack.length - 1].braces += 1;
    }

    out += ch;
    i += 1;
  }
  return out;
}

const BROWSER_AND_LANGUAGE_GLOBALS = new Set([
  // Keywords that parse as `name(`. "async" is here for `async (e) => {}`,
  // which looks exactly like a call to a function named async.
  "if", "for", "while", "switch", "catch", "return", "typeof", "function",
  "await", "new", "delete", "void", "in", "of", "do", "else", "yield",
  "async", "super", "this", "import", "throw", "case",
  // Standard library
  "Array", "Boolean", "Date", "Error", "Intl", "JSON", "Map", "Math", "Number",
  "Object", "Promise", "RegExp", "Set", "String", "Symbol", "BigInt",
  "parseInt", "parseFloat", "isNaN", "isFinite", "structuredClone",
  "encodeURIComponent", "decodeURIComponent", "encodeURI", "decodeURI",
  // Browser
  "window", "document", "console", "fetch", "alert", "confirm", "prompt",
  "setTimeout", "clearTimeout", "setInterval", "clearInterval", "queueMicrotask",
  "requestAnimationFrame", "cancelAnimationFrame", "FormData", "URLSearchParams",
  "CSS", "Event", "CustomEvent", "MutationObserver", "IntersectionObserver",
  "sessionStorage", "localStorage", "Image", "Blob", "URL", "AbortController",
]);

test("every function app.js calls is actually defined", () => {
  const code = stripNonCode(app);

  const defined = new Set();
  // function foo(...)
  for (const m of code.matchAll(/function\s+([A-Za-z_$][\w$]*)/g)) defined.add(m[1]);
  // const foo = ... / let foo = ... / var foo = ...  (covers arrow functions)
  for (const m of code.matchAll(/(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=/g)) defined.add(m[1]);
  // Destructured bindings: const { a, b } = ... and const [a, b] = ...
  for (const m of code.matchAll(/(?:const|let|var)\s*[[{]([^}\]]*)[\]}]\s*=/g)) {
    for (const part of m[1].split(",")) {
      const name = part.split(":").pop().trim().replace(/^\.\.\./, "").split("=")[0].trim();
      if (/^[A-Za-z_$][\w$]*$/.test(name)) defined.add(name);
    }
  }

  // Bare calls: `foo(` not preceded by a dot, and not a property shorthand.
  const called = new Set();
  for (const m of code.matchAll(/(?<![.\w$])([a-z_$][A-Za-z0-9_$]*)\s*\(/g)) called.add(m[1]);

  const missing = [...called].filter(
    (name) => !defined.has(name) && !BROWSER_AND_LANGUAGE_GLOBALS.has(name),
  );

  assert.deepEqual(
    missing,
    [],
    `app.js calls ${missing.join(", ")}, which nothing defines. ` +
      "A call like this throws a ReferenceError at runtime, and the catch " +
      "blocks around the render path will hide it.",
  );
});

test("every element id app.js reaches for exists in index.html", () => {
  const code = stripNonCode(app);
  const original = app;

  // $("#thing") and document.getElementById("thing"), read off the original
  // source because stripNonCode blanks the string holding the id.
  const wanted = new Set();
  for (const m of original.matchAll(/\$\(\s*["'`]#([A-Za-z0-9_-]+)["'`]\s*\)/g)) wanted.add(m[1]);
  for (const m of original.matchAll(/getElementById\(\s*["'`]([A-Za-z0-9_-]+)["'`]\s*\)/g)) wanted.add(m[1]);

  const present = new Set([...html.matchAll(/\bid="([^"]+)"/g)].map((m) => m[1]));
  const missing = [...wanted].filter((id) => !present.has(id)).sort();

  assert.deepEqual(
    missing,
    [],
    `app.js looks up #${missing.join(", #")}, which index.html does not contain. ` +
      "The lookup returns null and the next property access throws.",
  );
  assert.ok(code.length > 0);
});

test("the risk and explainability surfaces are wired to real fields", () => {
  // The three tail-risk tiles, the underwater chart, the execution panel and
  // the per-stock score breakdown each have to read a field the API actually
  // returns. Renaming one on the backend without the other should fail here.
  for (const field of [
    "var_95", "cvar_95", "beta_vs_benchmark",
    "total_turnover", "transaction_costs", "average_cash_weight", "rebalance_count",
  ]) {
    assert.match(app, new RegExp(`\\b${field}\\b`), `app.js never reads ${field}`);
  }

  // The underwater curve closes its area up to the zero line, not down to the
  // floor; closing downward shades the healthy days instead of the drawdown.
  assert.match(app, /L 780 20 L 54 20 Z/, "drawdown area must close to the zero line");

  for (const id of ["drawdown-card", "drawdown-path", "drawdown-caption", "drawdown-axis",
                    "execution-panel", "execution-stats"]) {
    assert.match(html, new RegExp(`id="${id}"`), `index.html is missing #${id}`);
  }

  // Sector tabs are built from the returned data. A hardcoded list drifts:
  // the old one offered Healthcare, which no security had.
  assert.match(app, /function renderSectorTabs/);
  assert.doesNotMatch(html, /data-sector="Healthcare"/);
});
