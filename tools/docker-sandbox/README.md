# Docker verification in a sandbox without registry access

The project's build sandbox could not reach any container registry, so `node:22-bookworm-slim` and
`python:3.13-slim-bookworm` could not be pulled. To still exercise the real `Dockerfile` end to end, it was
built on **stand-in base images** created from the sandbox's own Ubuntu 24.04 root filesystem
(Python 3.13, Node 22, with no third-party Python packages), via the Dockerfile's `NODE_IMAGE` /
`PYTHON_IMAGE` build args. Everything else (npm ci + UI build, pip install of every pinned dependency
from PyPI, native-library import check, non-root user, healthcheck, volume, in-image test suite) ran for real.

You do **not** need any of this on a normal machine: `docker compose up --build` pulls the official bases.

Reproduce (sandbox only):

```bash
# 1. stand-in bases (see the two tiny Dockerfiles in this folder; standin-rootfs:local = `docker import` of the host rootfs)
docker build -f Dockerfile.python -t standin-python:3.13 .
docker build -f Dockerfile.node   -t standin-node:22 .
# 2. the real image / tests, routed through the sandbox proxy (proxy + CA are build-time only, not stored in the image)
docker compose -f docker-compose.yml -f tools/docker-sandbox/compose.standin.yml up --build -d
docker build --network host --target test --build-arg NODE_IMAGE=standin-node:22 --build-arg PYTHON_IMAGE=standin-python:3.13 \
  --build-arg HTTPS_PROXY=$HTTPS_PROXY --build-arg NO_PROXY=127.0.0.1,localhost --secret id=extra_ca,src=/root/.ccr/ca-bundle.crt .
```
