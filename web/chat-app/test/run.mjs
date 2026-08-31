/* Bundles the smoke test with esbuild (JSX, node platform, happy-dom kept
   external) and runs it. `npm test`. */

import { build } from "esbuild";
import { spawnSync } from "child_process";
import { dirname, join } from "path";
import { fileURLToPath } from "url";
import { rmSync } from "fs";

const here = dirname(fileURLToPath(import.meta.url));
const out = join(here, ".smoke.bundle.mjs");

await build({
  entryPoints: [join(here, "smoke.test.jsx")],
  bundle: true,
  format: "esm",
  platform: "node",
  jsx: "automatic",
  outfile: out,
  external: ["happy-dom"],
  logLevel: "warning",
});

const res = spawnSync(process.execPath, [out], { stdio: "inherit", cwd: join(here, "..") });
rmSync(out, { force: true });
process.exit(res.status ?? 1);
