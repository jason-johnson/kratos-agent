import { createHash } from "node:crypto";
import { readdir, readFile, writeFile } from "node:fs/promises";
import { extname, join } from "node:path";

const [configPath, exportPath, outputPath] = process.argv.slice(2);
if (!configPath || !exportPath || !outputPath) {
  throw new Error("Usage: node generate-csp-config.mjs <nginx.conf> <export-dir> <output>");
}

async function collectHtmlFiles(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = await Promise.all(
    entries.map((entry) => {
      const path = join(directory, entry.name);
      return entry.isDirectory() ? collectHtmlFiles(path) : [path];
    }),
  );
  return files.flat().filter((path) => extname(path) === ".html");
}

const hashes = new Set();
for (const htmlPath of await collectHtmlFiles(exportPath)) {
  const html = await readFile(htmlPath, "utf8");
  for (const match of html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi)) {
    const digest = createHash("sha256").update(match[1]).digest("base64");
    hashes.add(`'sha256-${digest}'`);
  }
}

if (hashes.size === 0) {
  throw new Error("No inline scripts were found in the static export");
}

const config = await readFile(configPath, "utf8");
const marker = "__INLINE_SCRIPT_HASHES__";
if (!config.includes(marker)) {
  throw new Error(`Missing ${marker} in ${configPath}`);
}

await writeFile(outputPath, config.replace(marker, [...hashes].sort().join(" ")));
