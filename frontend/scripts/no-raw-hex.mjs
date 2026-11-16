// SPEC §9.6: no one-off colours. Raw hex colours may appear only in the token files.
// Run by `npm run lint`. Exits 1 and lists every offending line.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

const ROOT = new URL("..", import.meta.url).pathname;
const ALLOWED = new Set(["src/styles/tokens.css"]);
const HEX = /#[0-9a-fA-F]{3,8}\b/g;
const SCANNED = /\.(css|ts|tsx|html)$/;

function* files(dir) {
  for (const name of readdirSync(dir)) {
    if (name === "node_modules" || name === "dist" || name.startsWith(".")) continue;
    const path = join(dir, name);
    if (statSync(path).isDirectory()) yield* files(path);
    else if (SCANNED.test(name)) yield path;
  }
}

const problems = [];
for (const path of files(ROOT)) {
  const rel = relative(ROOT, path).replaceAll("\\", "/");
  if (ALLOWED.has(rel) || rel.endsWith(".d.ts")) continue;
  readFileSync(path, "utf8").split("\n").forEach((line, i) => {
    // Ignore SVG fragment references such as href="#i-chevron": they start with a letter
    // outside a-f or are followed by "-".
    for (const match of line.matchAll(HEX)) {
      const after = line[match.index + match[0].length];
      if (after === "-") continue;
      problems.push(`${rel}:${i + 1}: raw colour ${match[0]}`);
    }
  });
}
if (problems.length) {
  console.error(problems.join("\n"));
  console.error(`\n${problems.length} raw colour(s) outside the token files. Use a token.`);
  process.exit(1);
}
console.log("no-raw-hex: ok");
