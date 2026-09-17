/* Happy Bite — build-time performance plugin. No dependencies: node:zlib only.

   1. The catalogue is split. shared/catalog.json holds the ~50 hand-written
      ingredients and ~3,000 imported from the recipe corpus. Inlined whole it
      was 366 kB of the 613 kB main bundle — parsed before the first paint on
      every visit. The core stays in the bundle; the corpus becomes a compact
      JSON asset (arrays, no keys, no server-only `added`/`uses`), fetched in
      parallel with the bundle and merged before the kitchen opens.
   2. index.html: the stylesheet stops blocking the first paint (the static
      boot screen is styled by the inline critical CSS), and the Latin font and
      the corpus are preloaded so they download alongside the bundle.
   3. Every text asset is written pre-compressed (.br, .gz) for the server to
      send as-is — no compression work per request.

   shared/*.json is never modified: the server reads the same files. */

import { readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { brotliCompressSync, constants, gzipSync } from "node:zlib";

const HERE = dirname(fileURLToPath(import.meta.url));
const CATALOG = resolve(HERE, "../shared/catalog.json");
const CATALOG_ID = "\0hb-catalog";
const DEV_CORPUS = "/@hb/catalog-corpus.json";

const COMPACT = ["name", "category", "unit", "shelfLife"];
const SERVER_ONLY = new Set(["added", "uses"]);
const COMPRESSIBLE = /\.(js|css|html|json|svg|webmanifest|txt)$/;

function splitCatalog() {
  const c = JSON.parse(readFileSync(CATALOG, "utf8"));
  const core = {};
  const corpus = [];
  for (const [id, v] of Object.entries(c.ingredients)) {
    const keys = Object.keys(v).filter(k => !SERVER_ONLY.has(k));
    // Only plain corpus rows go compact; anything carrying extra fields stays whole.
    if (v.added && keys.every(k => COMPACT.includes(k))) {
      corpus.push([id, ...COMPACT.map(k => v[k])]);
    } else {
      core[id] = Object.fromEntries(keys.map(k => [k, v[k]]));
    }
  }
  return {
    core: { categories: c.categories, startingStock: c.startingStock, ingredients: core },
    corpus: JSON.stringify(corpus)
  };
}

function* files(dir) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) yield* files(p);
    else yield p;
  }
}

export default function happyBitePerf() {
  let build = false;
  let outDir = "dist";

  return {
    name: "happybite-perf",
    enforce: "pre",

    configResolved(config) {
      build = config.command === "build";
      outDir = resolve(config.root, config.build.outDir);
    },

    resolveId(source, importer) {
      if (!importer || !source.endsWith(".json")) return null;
      const target = resolve(dirname(importer.split("?")[0]), source);
      if (target === CATALOG) return CATALOG_ID;
      return null;
    },

    load(id) {
      if (id === CATALOG_ID) {
        this.addWatchFile(CATALOG);
        const { core, corpus } = splitCatalog();
        const url = build
          ? `import.meta.ROLLUP_FILE_URL_${this.emitFile({ type: "asset", name: "catalog-corpus.json", source: corpus })}`
          : JSON.stringify(DEV_CORPUS);
        // JSON.parse of a string literal parses markedly faster than an object literal.
        return `export default { ...JSON.parse(${JSON.stringify(JSON.stringify(core))}), corpusUrl: ${url} };`;
      }
      return null;
    },

    configureServer(server) {
      server.middlewares.use(DEV_CORPUS, (_req, res) => {
        res.setHeader("Content-Type", "application/json");
        res.end(splitCatalog().corpus);
      });
    },

    transformIndexHtml: {
      order: "post",
      handler(html, ctx) {
        if (!ctx.bundle) return html;
        const names = Object.keys(ctx.bundle);
        const font = names.find(n => /figtree-latin-wght-normal-[\w-]+\.woff2$/.test(n));
        const corpus = names.find(n => /catalog-corpus-[\w-]+\.json$/.test(n));
        const preloads = [
          font && `<link rel="preload" href="/${font}" as="font" type="font/woff2" crossorigin>`,
          corpus && `<link rel="preload" href="/${corpus}" as="fetch" crossorigin>`
        ].filter(Boolean).join("\n  ");

        return html
          // Non-blocking stylesheet: main.jsx waits for it before mounting React.
          .replace(/<link rel="stylesheet" crossorigin href="([^"]+)">/g,
            `<link rel="preload" as="style" crossorigin href="$1" data-app-css onload="this.onload=null;this.rel='stylesheet'">`)
          .replace("<!--preloads-->", preloads);
      }
    },

    closeBundle() {
      if (!build) return;
      for (const file of files(outDir)) {
        if (!COMPRESSIBLE.test(file)) continue;
        const data = readFileSync(file);
        if (data.length < 1024) continue;
        writeFileSync(file + ".br", brotliCompressSync(data, {
          params: { [constants.BROTLI_PARAM_QUALITY]: 11, [constants.BROTLI_PARAM_SIZE_HINT]: data.length }
        }));
        writeFileSync(file + ".gz", gzipSync(data, { level: 9 }));
      }
    }
  };
}
