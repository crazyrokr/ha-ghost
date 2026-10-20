FROM ghcr.io/hassio-addons/debian-base:32770e4

COPY root /

RUN apt-get update && apt-get install -y --no-install-recommends npm nodejs su-exec && rm -rf /var/lib/apt/lists/*

WORKDIR /var/lib/ghost
RUN npm install ghost-cli@latest -g

LABEL \
  org.opencontainers.image.title="Home Assistant Ghost" \
  org.opencontainers.image.description="Runs Ghost within your Home Assistant setup providing integration of live-features" \
  org.opencontainers.image.source="https://github.com/crazyrokr/ha-ghost/tree/main/ghost" \
  org.opencontainers.image.licenses="MIT"
