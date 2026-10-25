FROM python:3.11-slim

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
