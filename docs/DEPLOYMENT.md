# Deployment validation

ECSAE has two intentionally separate runtime profiles:

- The default API serves structured audits and the deterministic `/extract` fallback. It is
  suitable for the latency benchmark and does not download model weights at startup.
- The `transformer` Compose profile builds `Dockerfile.extract`, downloads the exact GLiNER
  revision during image construction, verifies the model files, and runs with Hugging Face
  offline mode enabled. The transformer endpoint is an extraction service, not a clinical
  accuracy claim and not part of the `/audit` latency benchmark.

## Local validation

Run the default service smoke test on a Docker-capable Linux host or CI runner:

```bash
docker build --check .
docker compose build api
docker compose up -d api redis
curl --fail http://localhost:8000/health
curl --fail http://localhost:8000/ready
curl --fail -H 'content-type: application/json' \
  --data '{"study_id":"smoke","randomized_n":20,"arm_ns":{"a":10,"b":10}}' \
  http://localhost:8000/audit
docker compose down --volumes
```

The same probes run in `.github/workflows/ci.yml`. A successful image build alone is not
deployment evidence; retain the workflow run, image digest, commit SHA, configuration version,
rules version, model revision, and validation timestamp for a release record.

The transformer profile requires the model download to succeed while building. After it starts,
`/ready` reports `transformer: ready`; the default profile reports `transformer: offline` by design.
Do not claim transformer quality from the checked-in synthetic smoke artifact. Add a frozen,
real-text, trial-ID-separated held-out set and independent span labels before reporting extraction
precision, recall, or F1 as product performance.

## Production controls

Put TLS termination, authentication, network policy, request rate limiting, secret management,
Redis authentication, resource limits, and rollback policy at the deployment boundary. Compose is
a reproducible local/CI smoke environment, not a production orchestrator. Redis is an availability
optimization: cache failure must not change audit semantics, and cached payloads should be treated
as sensitive if the service handles non-public inputs.