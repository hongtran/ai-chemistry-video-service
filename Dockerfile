# syntax=docker/dockerfile:1
#
# Single image that runs the FastAPI service AND the hyperframes render step.
# The render side (hyperframes CLI + headless Chromium + ffmpeg) is the fragile
# part, so we base off Node and mirror hyperframes' own dist/docker/Dockerfile.render
# for the Chromium setup, then add Python for the API.

FROM node:22-bookworm-slim

# Keep in sync with build-video.sh / render_kit/package.json (they call
# `npx hyperframes@0.7.18`); pinning it globally avoids a re-download per render.
ARG HYPERFRAMES_VERSION=0.7.18

ENV DEBIAN_FRONTEND=noninteractive

# System deps: Python (for the service) + ffmpeg, system Chromium and the
# exact lib/font set hyperframes needs for rendering (mirrors their Dockerfile.render).
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-venv python3-pip \
      ca-certificates curl unzip ffmpeg chromium \
      libgbm1 libnss3 libatk-bridge2.0-0 libdrm2 libxcomposite1 \
      libxdamage1 libxrandr2 libcups2 libasound2 libpangocairo-1.0-0 \
      libxshmfence1 libgtk-3-0 \
      fonts-liberation fonts-noto-color-emoji fonts-noto-cjk fonts-noto-core \
      fonts-noto-extra fonts-noto-ui-core fonts-freefont-ttf fonts-dejavu-core \
      fontconfig \
    && rm -rf /var/lib/apt/lists/* && apt-get clean && fc-cache -fv

# Use the distro Chromium instead of a runtime browser download. CONTAINER=true
# lets the hyperframes engine detect the sandboxed environment and pass the
# right Chromium flags (--no-sandbox etc.) automatically.
ENV PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true \
    PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium \
    CONTAINER=true \
    HYPERFRAMES_NO_UPDATE_CHECK=1 \
    HYPERFRAMES_NO_TELEMETRY=1

# Pin the hyperframes CLI so build-video.sh's `npx hyperframes@0.7.18` resolves
# it locally instead of fetching it on every render.
RUN npm install -g hyperframes@${HYPERFRAMES_VERSION}

# Python service in an isolated venv (Debian marks the system env PEP-668
# externally-managed, so a plain `pip install` would be refused).
ENV VIRTUAL_ENV=/opt/venv
RUN python3 -m venv "$VIRTUAL_ENV"
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

WORKDIR /app

# Install Python deps first for layer caching.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# App source (HYPERFRAMES_DIR defaults to ./render_kit, resolved against /app).
COPY . .

EXPOSE 8000

# Cheap liveness probe: FastAPI always serves /openapi.json.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -fsS http://localhost:8000/openapi.json >/dev/null || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
