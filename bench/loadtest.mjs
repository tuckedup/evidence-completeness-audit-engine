// Low-overhead open-loop HTTP generator. Uses raw JSONL lines for cache-hit requests.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { performance } from "node:perf_hooks";
import { createHash, randomUUID } from "node:crypto";

const argv = Object.fromEntries(process.argv.slice(2).reduce((pairs, value, index, all) => {
  if (value.startsWith("--")) pairs.push([value.slice(2), all[index + 1]]);
  return pairs;
}, []));
const url = argv.url ?? "http://127.0.0.1:8000/audit";
const payloadPath = argv.payloads ?? "data/corpus/ctg-randomized-2000.jsonl";
const scenario = argv.scenario;
if (!["cold", "mixed", "warm"].includes(scenario)) throw new Error("--scenario cold|mixed|warm is required");
const requests = Number(argv.requests ?? 20000);
const rate = Number(argv.rate ?? 200);
const concurrency = Number(argv.connections ?? 128);
const hitRatio = Number(argv["hit-ratio"] ?? 0.9);
const output = argv.output;
if (!output) throw new Error("--output is required");
const runId = argv["run-id"] ?? randomUUID();
const raw = fs.readFileSync(payloadPath, "utf8").trim();
const payloadSha256 = createHash("sha256").update(fs.readFileSync(payloadPath)).digest("hex");
const payloads = payloadPath.endsWith(".jsonl")
  ? raw.split(/\r?\n/).filter(Boolean)
  : (Array.isArray(JSON.parse(raw)) ? JSON.parse(raw) : [JSON.parse(raw)]).map(JSON.stringify);
const latencies = [];
const serverLatencies = [];
const errors = [];
const cacheHeaders = {};

function withNonce(payload, nonce) {
  const value = JSON.parse(payload);
  value.metadata = { ...(value.metadata ?? {}), benchmark_nonce: nonce };
  return JSON.stringify(value);
}

function bodyFor(index) {
  const base = payloads[index % payloads.length];
  if (scenario === "cold") return withNonce(base, `${runId}:cold:${index}`);
  if (scenario === "mixed") {
    const targetMisses = Math.round(requests * (1 - hitRatio));
    const miss = Math.floor((index + 1) * targetMisses / requests) > Math.floor(index * targetMisses / requests);
    if (miss) return withNonce(base, `${runId}:miss:${index}`);
  }
  return base;
}

async function post(body) {
  const response = await fetch(url, { method: "POST", headers: { "content-type": "application/json" }, body });
  await response.arrayBuffer();
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response;
}

async function pool(items, worker) {
  let next = 0;
  await Promise.all(Array.from({ length: Math.min(concurrency, items.length) }, async () => {
    while (true) {
      const index = next++;
      if (index >= items.length) return;
      await worker(items[index], index);
    }
  }));
}

function percentile(values, quantile) {
  if (!values.length) return null;
  const ordered = [...values].sort((a, b) => a - b);
  return ordered[Math.max(0, Math.min(ordered.length - 1, Math.ceil(ordered.length * quantile) - 1))];
}

const versionUrl = new URL("/version", url).toString();
const serviceVersion = await (await fetch(versionUrl)).json();
if (scenario === "warm" || scenario === "mixed") await pool(payloads, post);
const scheduledStart = performance.now() + 250;
let inFlight = 0;
let next = 0;
await new Promise((resolve) => {
  const launch = () => {
    const now = performance.now();
    while (next < requests && inFlight < concurrency && scheduledStart + next * 1000 / rate <= now) {
      const index = next++;
      const scheduled = scheduledStart + index * 1000 / rate;
      inFlight++;
      post(bodyFor(index)).then((response) => {
        const done = performance.now();
        latencies.push(done - scheduled);
        const cache = response.headers.get("x-ecsae-cache") ?? "absent";
        cacheHeaders[cache] = (cacheHeaders[cache] ?? 0) + 1;
        const server = response.headers.get("x-ecsae-server-ms");
        if (server !== null) serverLatencies.push(Number(server));
      }).catch((error) => errors.push(String(error))).finally(() => {
        inFlight--;
        if (next === requests && inFlight === 0) resolve();
        else setImmediate(launch);
      });
    }
    if (next < requests) setTimeout(launch, Math.max(0, scheduledStart + next * 1000 / rate - performance.now()));
    else if (inFlight === 0) resolve();
  };
  launch();
});
const durationSeconds = (performance.now() - scheduledStart) / 1000;
const clientP95 = percentile(latencies, 0.95);
const serverP95 = percentile(serverLatencies, 0.95);
const report = {
  schema_version: "3",
  generated_at: new Date().toISOString(),
  generator: `Node ${process.version} built-in fetch; raw JSONL hit bodies`,
  model: "open-loop fixed-rate; latency = completion - scheduled_send",
  run_id: runId, scenario, expected_hit_fraction: scenario === "cold" ? 0 : scenario === "warm" ? 1 : hitRatio,
  payload_source: fs.realpathSync(payloadPath), distinct_base_payloads: payloads.length,
  payload_sha256: payloadSha256, service_version: serviceVersion,
  generator_and_server_colocated: true, server_workers: Number(argv["server-workers"] ?? 1),
  url, requested: requests, completed: latencies.length, errors: errors.length, error_examples: errors.slice(0, 10),
  target_rate_per_second: rate, achieved_rate_per_second: latencies.length / Math.max(durationSeconds, 1e-9),
  duration_seconds: durationSeconds, connections: concurrency, cache_headers: cacheHeaders,
  latency_ms: { min: Math.min(...latencies), mean: latencies.reduce((a, b) => a + b, 0) / latencies.length,
    p50: percentile(latencies, .5), p95: clientP95, p99: percentile(latencies, .99), max: Math.max(...latencies) },
  server_handler_latency_ms: { observations: serverLatencies.length,
    mean: serverLatencies.reduce((a, b) => a + b, 0) / serverLatencies.length,
    p50: percentile(serverLatencies, .5), p95: serverP95, p99: percentile(serverLatencies, .99), max: Math.max(...serverLatencies) },
  client_to_server_p95_ratio: clientP95 && serverP95 ? clientP95 / serverP95 : null,
  environment: { platform: `${os.platform()} ${os.release()}`, cpu_count: os.cpus().length, node: process.version },
};
fs.mkdirSync(path.dirname(output), { recursive: true });
fs.writeFileSync(output, JSON.stringify(report, null, 2) + "\n");
console.log(JSON.stringify({ cache: cacheHeaders, client_p95_ms: clientP95, server_p95_ms: serverP95,
  achieved_rps: report.achieved_rate_per_second, errors: errors.length }));
process.exitCode = errors.length ? 1 : 0;
