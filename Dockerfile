# Kenny — shared demo image.
#
# Two-stage on purpose. docling drags in torch (~2GB of build tooling and wheels); the
# runtime only needs the installed packages, the cached model weights and the ingested
# corpus. Splitting keeps the shipped image to roughly what actually runs.
FROM python:3.11-slim AS build

ENV PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/opt/models

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# CPU-only torch. The default wheel bundles CUDA (~2GB) that no demo host has a GPU for.
COPY requirements.txt .
RUN pip install --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.txt

COPY . .

# Generate the reference PDFs, then parse the whole corpus and cache the models INTO the
# image. Runs with no API key: reproducible, free, and nothing secret lands in a layer.
RUN python -m scripts.prepare_deploy cases/santacruz

# --------------------------------------------------------------------------- #
FROM python:3.11-slim

# Auth is OFF by default — the app serves openly, like a local run. To lock a deploy,
# set KENNY_REQUIRE_AUTH=1 plus the three KENNY_* secrets (see core/auth.py); the app
# then refuses to boot half-configured. The chat rate limit is active either way.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/opt/models \
    CASE_NAME=santacruz

RUN apt-get update && apt-get install -y --no-install-recommends \
      libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 10001 kenny

COPY --from=build /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=build /usr/local/bin /usr/local/bin
COPY --from=build /opt/models /opt/models
COPY --from=build /app /app

# The app writes the ledger, catalog and rule library, so it owns the case tree and the
# volume — but not its own source. Nothing at runtime should be able to rewrite core/.
RUN mkdir -p /data && chown -R kenny:kenny /data /app/cases && chmod +x /app/scripts/entrypoint.sh

WORKDIR /app
# Container STARTS as root because platform-mounted volumes (Railway) arrive root-owned
# and there is no pre-mount hook to chown them; the entrypoint fixes /data ownership and
# immediately drops to the unprivileged `kenny` user before serving. Fly chowns volumes
# to the image user so this was invisible there — Railway does not.
EXPOSE 8080

# Reports the ledger chain, so a corrupted audit trail marks the instance unhealthy.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz').status==200 else 1)"

ENTRYPOINT ["/app/scripts/entrypoint.sh"]
