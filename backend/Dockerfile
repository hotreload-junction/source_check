# CiteTrace backend. Build context: backend/ (deploy/docker-compose.yml passes GIT_SHA).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first (own layer, cached until pyproject.toml changes): read them from pyproject.toml so the
# list lives in one place and no app code is needed to install them.
COPY pyproject.toml ./
RUN python -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']))" > /tmp/requirements.txt \
 && pip install -r /tmp/requirements.txt && rm /tmp/requirements.txt

COPY app ./app
# demo cache: cases.json (+ results/*.json when present); served by /api/demo with every external service down
COPY demo ./demo

# Shown by GET /api/health ("version"); config comes only from the environment (no .env in the image).
ARG GIT_SHA=dev
ENV GIT_SHA=${GIT_SHA}

RUN useradd --system --uid 10001 --no-create-home app
USER app

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
