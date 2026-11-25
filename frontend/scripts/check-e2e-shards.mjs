import { execFileSync, spawnSync } from "node:child_process";
import { readFileSync, rmSync } from "node:fs";
import { resolve } from "node:path";
import process from "node:process";

const [shard, count] = process.argv.slice(2).map(Number);
if (!Number.isSafeInteger(shard) || !Number.isSafeInteger(count) || shard < 0 || shard > count || count < 1) {
  throw new Error("Usage: node scripts/check-e2e-shards.mjs <shard (0 = list only)> <shard count>");
}

const cli = ["node_modules/@playwright/test/cli.js", "test"];
const env = { ...process.env };
delete env.PLAYWRIGHT_JSON_OUTPUT_FILE;
delete env.PLAYWRIGHT_JSON_OUTPUT_NAME;
delete env.PLAYWRIGHT_JSON_OUTPUT_DIR;

// A JSON report can describe nested suites and the same spec in different projects.
function testsIn(suites) {
  return suites.flatMap((suite) => [
    ...suite.specs.flatMap((spec) =>
      spec.tests.map((test) => ({
        id: JSON.stringify([test.projectName, spec.id]),
        expectedStatus: test.expectedStatus,
        results: test.results,
      })),
    ),
    ...testsIn(suite.suites ?? []),
  ]);
}

function list(args) {
  const json = execFileSync(process.execPath, [...cli, "--list", "--reporter=json", ...args], {
    encoding: "utf8",
    env,
    stdio: ["ignore", "pipe", "inherit"],
  });
  return testsIn(JSON.parse(json).suites);
}

function requireSameTests(expected, actual, label) {
  const ids = new Set(expected.map((test) => test.id));
  if (ids.size !== expected.length || actual.length !== expected.length ||
      actual.some((test) => !ids.delete(test.id)) || ids.size !== 0) {
    throw new Error(`${label}: expected ${expected.length} unique tests, got ${actual.length} with different identities or duplicates`);
  }
}

const full = list([]);
const partitions = Array.from({ length: count }, (_, index) => {
  const number = index + 1;
  const tests = list([`--shard=${number}/${count}`]);
  if (!tests.length) throw new Error(`Shard ${number}/${count} is empty`);
  console.log(`Shard ${number}/${count}: ${tests.length} collected`);
  return tests;
});
requireSameTests(full, partitions.flat(), "Full collection versus shards");
console.log(`Verified all ${full.length} collected tests occur in exactly one shard`);

if (shard !== 0) {
  const reportPath = resolve(`test-results/e2e-shard-${shard}.json`);
  rmSync(reportPath, { force: true });
  const run = spawnSync(process.execPath, [
    ...cli, `--shard=${shard}/${count}`, `--output=test-results/shard-${shard}`,
  ], {
    stdio: "inherit",
    env: { ...env, PLAYWRIGHT_JSON_OUTPUT_FILE: reportPath },
  });
  if (run.error) throw run.error;
  const report = JSON.parse(readFileSync(reportPath, "utf8"));
  const actual = testsIn(report.suites);
  requireSameTests(partitions[shard - 1], actual, `Shard ${shard}/${count} execution`);
  console.log(JSON.stringify(report.stats));
  const expectedSkips = partitions[shard - 1].filter((test) => test.expectedStatus === "skipped");
  const actualSkips = actual.filter((test) => test.results[0]?.status === "skipped");
  requireSameTests(expectedSkips, actualSkips, `Shard ${shard}/${count} skips`);
  if (run.status !== 0 || report.stats.unexpected || report.stats.flaky ||
      actual.some((test) => test.results.length !== 1)) {
    process.exitCode = 1;
  }
}
