# The exported console is the same static files on every platform, so it is
# built once on the build host. None of its toolchain reaches the runtime image.
FROM --platform=$BUILDPLATFORM node:22.23.3-trixie-slim@sha256:28d03803f09ba76560e273af33beb7eda4ed68fa0698bc9bd15e9a6e35693923 AS console
ENV WRANGLER_SEND_METRICS=false
WORKDIR /console
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY tsconfig.json next.config.ts vite.config.ts postcss.config.mjs ./
COPY .openai/hosting.json .openai/hosting.json
COPY app ./app
COPY build ./build
COPY public ./public
COPY worker ./worker
RUN npm run build:console

FROM python:3.13.16-slim-trixie@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f AS build
ENV PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY requirements-build.lock requirements-runtime.lock requirements-fault-lab.lock ./
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --require-hashes --no-cache-dir -r requirements-build.lock \
    && /opt/venv/bin/pip install --require-hashes --no-cache-dir -r requirements-runtime.lock
COPY services/orchestrator/pyproject.toml services/orchestrator/LICENSE ./
COPY services/orchestrator/incident_copilot ./incident_copilot
RUN /opt/venv/bin/pip install --no-deps --no-build-isolation --no-cache-dir . \
    && /opt/venv/bin/pip check \
    && /opt/venv/bin/pip uninstall --yes pip hatchling editables pathspec pluggy tomlkit trove-classifiers

FROM python:3.13.16-slim-trixie@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
# Fix the exact Debian security revision; signed APT metadata checks the artifact.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends liblzma5=5.8.1-1+deb13u2 \
    && rm -rf /var/lib/apt/lists/* \
    && /usr/local/bin/python -c "import pathlib,shutil; p=pathlib.Path('/usr/local/lib/python3.13/site-packages'); [shutil.rmtree(x) for x in p.glob('pip*') if x.is_dir()]; [x.unlink() for x in pathlib.Path('/usr/local/bin').glob('pip*') if x.is_file() or x.is_symlink()]"
COPY --from=build /opt/venv /opt/venv
COPY LICENSE THIRD_PARTY_NOTICES.md /usr/share/doc/opspilot/
# Served from the API's own origin; set CONSOLE_STATIC_DIR= to turn the console off.
COPY --from=console /console/dist/console /app/console
ENV CONSOLE_STATIC_DIR=/app/console
WORKDIR /app
USER 65532:65532
EXPOSE 8080
CMD ["uvicorn", "incident_copilot.api:app", "--host", "0.0.0.0", "--port", "8080"]
