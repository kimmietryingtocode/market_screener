const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");

const root = __dirname;
const port = Number(process.env.PORT || 4173);
const types = {
  ".css": "text/css; charset=utf-8",
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
};

http.createServer((request, response) => {
  const pathname = decodeURIComponent(new URL(request.url, `http://${request.headers.host}`).pathname);
  const requested = pathname === "/" ? "/index.html" : pathname;
  const filePath = path.resolve(root, `.${requested}`);

  response.setHeader("Referrer-Policy", "no-referrer-when-downgrade");
  response.setHeader("Cross-Origin-Opener-Policy", "same-origin-allow-popups");
  response.setHeader("Cache-Control", "no-store");

  if (!filePath.startsWith(`${root}${path.sep}`)) {
    response.writeHead(403).end("Forbidden");
    return;
  }

  fs.stat(filePath, (error, stats) => {
    if (error || !stats.isFile()) {
      response.writeHead(404).end("Not found");
      return;
    }
    response.setHeader("Content-Type", types[path.extname(filePath)] || "application/octet-stream");
    response.writeHead(200);
    fs.createReadStream(filePath).pipe(response);
  });
}).listen(port, "127.0.0.1", () => {
  console.log(`Signalist frontend: http://localhost:${port}`);
});
