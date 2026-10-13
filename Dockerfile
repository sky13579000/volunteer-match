# EDM-AI: zero runtime dependencies for SQLite; DB drivers are opt-in build args.
FROM python:3.12-slim AS app
ARG EXTRAS=""            # e.g. --build-arg EXTRAS="postgres,pdf"  (comma separated: postgres, oracle, pdf)
ENV EDM_AI_STORE=/data/store.db PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
# PDF text extraction: the "pdf" extra (pypdf, pure pip wheel) works fully offline.
# Optionally add poppler's pdftotext (better layout) with --build-arg POPPLER=1 (needs an apt mirror).
ARG POPPLER=0
RUN if [ "$POPPLER" = "1" ]; then apt-get update && apt-get install -y --no-install-recommends poppler-utils \
    && rm -rf /var/lib/apt/lists/*; fi
WORKDIR /app
COPY pyproject.toml README.md ./
COPY edm_ai ./edm_ai
# Private package mirror / TLS-inspecting proxy? Pass its CA without disabling verification:
#   docker build --secret id=ca,src=/path/corp-ca.pem ...   (and --build-arg PIP_INDEX_URL=https://mirror/simple)
ARG PIP_INDEX_URL=""
RUN --mount=type=secret,id=ca,required=false \
    if [ -s /run/secrets/ca ]; then export PIP_CERT=/run/secrets/ca; fi; \
    if [ -n "$PIP_INDEX_URL" ]; then export PIP_INDEX_URL; fi; \
    pip install --no-cache-dir ".${EXTRAS:+[$EXTRAS]}"
RUN useradd --system --uid 10001 --home /data edm && mkdir -p /data && chown edm /data
USER edm
VOLUME /data
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request as u;u.urlopen('http://127.0.0.1:8080/api/health',timeout=2)"
ENTRYPOINT ["edm-ai"]
CMD ["-c", "/config/config.json", "serve", "--host", "0.0.0.0", "--port", "8080", "--ingest", "--reindex-every", "300"]
