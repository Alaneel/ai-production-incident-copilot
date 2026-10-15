FROM python:3.13.16-slim-trixie@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f AS build
ENV PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY requirements-build.lock requirements-runtime.lock requirements-fault-lab.lock ./
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --require-hashes --no-cache-dir -r requirements-build.lock \
    && /opt/venv/bin/pip install --require-hashes --no-cache-dir -r requirements-runtime.lock
COPY services/mcp-server/pyproject.toml services/mcp-server/LICENSE ./
COPY services/mcp-server/incident_mcp ./incident_mcp
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
WORKDIR /app
USER 65532:65532
EXPOSE 8082
CMD ["uvicorn", "incident_mcp.server:app", "--host", "0.0.0.0", "--port", "8082"]
