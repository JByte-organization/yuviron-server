# =============================================================================
# infra/docker/frontend-next/Dockerfile — Multi-stage Dockerfile для Next.js приложений.
#
# Один Dockerfile собирает любое из трёх фронтенд-приложений через ARG APP_NAME:
#   APP_NAME=admin      → собирает apps/admin/
#   APP_NAME=backoffice → собирает apps/backoffice/
#   APP_NAME=client-app → собирает apps/client-app/
#
# Стадии:
#   base    — Node 20 Alpine + corepack + pnpm 9
#   deps    — устанавливаем зависимости (pnpm install --frozen-lockfile)
#             BuildKit cache mount для /pnpm/store — пересборка только при изменении lock
#   build   — генерируем TypeScript API-клиент (pnpm api:gen через Orval)
#             затем собираем приложение (turbo run build --filter=<APP_NAME>...)
#   runtime — минимальный образ только с Next.js standalone (без node_modules!)
#             Standalone output = один server.js + минимальные зависимости
#
# Безопасность:
#   - Запускается от UID/GID 10001 (не root)
#   - NODE_ENV=production
#   - HEALTHCHECK: HTTP GET /
#
# Сборка задаётся через build args в compose.frontends.yml:
#   args: { APP_NAME: admin }
# =============================================================================
FROM node:20-alpine@sha256:fb4cd12c85ee03686f6af5362a0b0d56d50c58a04632e6c0fb8363f609372293 AS base
WORKDIR /repo
# corepack позволяет использовать pnpm нужной версии без глобальной установки
RUN corepack enable && corepack prepare pnpm@9.0.0 --activate

FROM base AS deps
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml turbo.json ./
COPY .npmrc ./
COPY apps/admin/package.json ./apps/admin/package.json
COPY apps/backoffice/package.json ./apps/backoffice/package.json
COPY apps/client-app/package.json ./apps/client-app/package.json
COPY packages/api/package.json ./packages/api/package.json
COPY packages/eslint-config/package.json ./packages/eslint-config/package.json
COPY packages/store/package.json ./packages/store/package.json
COPY packages/typescript-config/package.json ./packages/typescript-config/package.json
COPY packages/ui/package.json ./packages/ui/package.json
RUN --mount=type=cache,id=pnpm-store,target=/pnpm/store,sharing=locked \
    pnpm install --frozen-lockfile --store-dir /pnpm/store

FROM deps AS build
ARG APP_NAME
RUN test -n "$APP_NAME"
COPY orval.config.ts ./
COPY apps ./apps
COPY packages ./packages
RUN pnpm api:gen
RUN pnpm turbo run build --filter="${APP_NAME}..."

FROM node:20-alpine@sha256:fb4cd12c85ee03686f6af5362a0b0d56d50c58a04632e6c0fb8363f609372293 AS runtime
WORKDIR /app
ENV NODE_ENV=production
ARG APP_NAME
ENV APP_NAME=${APP_NAME}

RUN addgroup -S -g 10001 app \
    && adduser -S -D -H -u 10001 -G app app

COPY --chown=10001:10001 --from=build /repo/apps/${APP_NAME}/.next/standalone ./
COPY --chown=10001:10001 --from=build /repo/apps/${APP_NAME}/.next/static ./apps/${APP_NAME}/.next/static
COPY --chown=10001:10001 --from=build /repo/apps/${APP_NAME}/public ./apps/${APP_NAME}/public

ENV HOME=/tmp
ENV HOSTNAME=0.0.0.0
ENV PORT=3000

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD node -e "require('http').get('http://127.0.0.1:3000/',r=>process.exit(r.statusCode<400?0:1)).on('error',()=>process.exit(1))"

USER 10001:10001
CMD ["sh", "-c", "node apps/${APP_NAME}/server.js"]
