# syntax=docker/dockerfile:1

FROM node:20-alpine AS base
WORKDIR /repo
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
COPY apps ./apps
COPY packages ./packages
RUN pnpm turbo run build --filter="${APP_NAME}..."

FROM node:20-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production
ARG APP_NAME
ENV APP_NAME=${APP_NAME}

COPY --from=build /repo/apps/${APP_NAME}/.next/standalone ./
COPY --from=build /repo/apps/${APP_NAME}/.next/static ./apps/${APP_NAME}/.next/static
COPY --from=build /repo/apps/${APP_NAME}/public ./apps/${APP_NAME}/public

ENV HOSTNAME=0.0.0.0
ENV PORT=3000
CMD ["sh", "-c", "node apps/${APP_NAME}/server.js"]
