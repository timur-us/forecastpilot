# python:3.11-slim (3.11.17-slim-trixie, built 2026-10-21), pinned by the
# digest of its multi-platform index so the build is reproducible on every
# platform. Dependabot proposes new digests weekly (.github/dependabot.yml).
# A Dockerfile comment cannot share the FROM line, so the tag is named here.
FROM python:3.12-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704

WORKDIR /app

COPY requirements.txt .
# setuptools only builds packages; the service never imports it. The base
# image's copy carried two HIGH findings in its vendored jaraco.context and
# wheel (CVE-2026-23949, CVE-2026-24049), so it is removed after the install.
# CI starts the image and waits for /health before scanning it.
RUN pip install --no-cache-dir -r requirements.txt \
    && pip uninstall -y setuptools

COPY app/ ./app/

RUN useradd --create-home appuser
USER appuser

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
