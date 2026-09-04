# Benchmark evidence

The canonical submission suite uses all 2,000 hydrated registry payloads, a low-overhead Node
generator, one local Uvicorn worker, 20,000 requests per scenario, and 200 offered requests/s:

- `node-warm-hydrated-20k-200rps-local-windows.json`: 20,000 hits, zero errors; client/server p95
  28.37/1.83 ms.
- `node-mixed90-hydrated-20k-200rps-local-windows.json`: exactly 18,000 hits and 2,000 misses,
  zero errors; client/server p95 36.65/7.63 ms.
- `node-cold-hydrated-20k-200rps-local-windows.json`: 20,000 unique misses, zero errors;
  client/server p95 4734.21/1153.45 ms.

All client latencies are completion minus scheduled-send time, including queueing. Server timing
comes from `X-ECSAE-Server-Ms`. The warm run is the cache-hit service claim; the cold and mixed
artifacts are retained separately so miss-path cost is visible rather than blended away.

Warm-cache knee probing on this host shows a sustained 1,000 rps run with zero errors and a 1,200
rps run that degrades. The 1,100 rps point is intentionally omitted from the repository and is not
part of the canonical review package. Linux/Docker four-worker validation is still pending because
no Docker, Podman, or usable WSL runtime exists on this host.
