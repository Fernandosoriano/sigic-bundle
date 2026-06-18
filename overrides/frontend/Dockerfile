# 🏗️ Build stage
FROM node:22 AS builder

ARG NUXT_PUBLIC_APP_BASE_PATH
ARG NUXT_PUBLIC_AUTH_BASE_URL
ARG NUXT_PUBLIC_BASE_URL

ENV NUXT_PUBLIC_APP_BASE_PATH=${NUXT_PUBLIC_APP_BASE_PATH:-/}
ENV NUXT_PUBLIC_AUTH_BASE_URL=${NUXT_PUBLIC_AUTH_BASE_URL:-http://localhost:3000/api/auth}
ENV NUXT_PUBLIC_BASE_URL=${NUXT_PUBLIC_BASE_URL:-http://localhost:3000}

ENV NODE_ENV=production

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    make \
    g++ \
    libsqlite3-dev \
    && rm -rf /var/lib/apt/lists/*

COPY package*.json ./

RUN npm install --include=dev

COPY . .

# Overlays de plataforma — sobreescriben archivos del código base sin modificar el submodulo
COPY --from=platform_overrides . .

RUN npm run build


# 🚀 Final stage
FROM node:22-slim

ENV NODE_ENV=production

WORKDIR /app

COPY --from=builder /app/.output/ .output/

# ipx es peer dependency de @nuxt/image y no queda bundleado por Nitro;
# se instala explícitamente para garantizar disponibilidad en runtime
RUN npm install ipx --no-save

EXPOSE 3000

CMD ["node", ".output/server/index.mjs"]
