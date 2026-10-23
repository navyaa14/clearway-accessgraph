# ClearWay AccessGraph — container image
#
#   docker compose up --build                      # build + run on http://localhost:8000
#   docker build -t clearway .                     # image only
#   docker build --target test -t clearway:test .  # also runs the full test suite inside the image
#
# Base images are pinned to a Debian release so package/library names cannot shift under us.
# Override them (e.g. an internal registry mirror) with --build-arg NODE_IMAGE=... / PYTHON_IMAGE=...
# Behind a TLS-intercepting proxy, pass its CA for the dependency downloads only (not kept in the image):
#   docker build --secret id=extra_ca,src=/path/to/proxy-ca.pem .
# No OS packages are installed: opencv-python-headless, PyAV and aiortc wheels bundle their native
# libraries and need only libc/libstdc++/libz, which the python:slim base already provides.

ARG NODE_IMAGE=node:22-bookworm-slim
ARG PYTHON_IMAGE=python:3.13-slim-bookworm

# ---------------------------------------------------------------- UI build
FROM ${NODE_IMAGE} AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=secret,id=extra_ca \
    if [ -s /run/secrets/extra_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/extra_ca; fi; \
    npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------------------------------------------------------------- runtime
FROM ${PYTHON_IMAGE} AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CLEARWAY_DATA_DIR=/data \
    CLEARWAY_FRONTEND_DIST=/app/frontend/dist
WORKDIR /app/backend
COPY backend/requirements.txt backend/requirements-dev.txt backend/requirements.lock ./
RUN --mount=type=secret,id=extra_ca \
    if [ -s /run/secrets/extra_ca ]; then export PIP_CERT=/run/secrets/extra_ca; fi; \
    python -m pip install --no-cache-dir -r requirements.txt -c requirements.lock \
 && python -c "import cv2, av, aiortc, shapely, fastapi; print('native deps OK, OpenCV', cv2.__version__)"
COPY backend/ ./
COPY --from=ui /ui/dist /app/frontend/dist
RUN mkdir -p /home \
 && useradd --create-home --uid 10001 --user-group clearway \
 && mkdir -p /data \
 && chown clearway:clearway /data
EXPOSE 8000

# ---------------------------------------------------------------- tests (opt-in: --target test)
FROM runtime AS test
USER root
RUN --mount=type=secret,id=extra_ca \
    if [ -s /run/secrets/extra_ca ]; then export PIP_CERT=/run/secrets/extra_ca; fi; \
    python -m pip install --no-cache-dir -r requirements-dev.txt -c requirements.lock
USER clearway
ENV CLEARWAY_DATA_DIR=/tmp/clearway-test-data
RUN python -m pytest -q -p no:cacheprovider

# ---------------------------------------------------------------- final image (default target)
FROM runtime AS app
USER clearway
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
