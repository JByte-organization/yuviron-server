# syntax=docker/dockerfile:1

ARG APP_NAME

FROM node:20-alpine AS base
WORKDIR /repo
RUN corepack enable && corepack prepare pnpm@9.0.0 --activate

FROM base AS deps
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml turbo.json ./
COPY apps ./apps
COPY packages ./packages
RUN pnpm install --frozen-lockfile

FROM deps AS build
ARG APP_NAME
COPY . .
RUN pnpm turbo run build --filter=${APP_NAME}

FROM node:20-alpine AS runner
WORKDIR /app
RUN corepack enable && corepack prepare pnpm@9.0.0 --activate

ARG APP_NAME

COPY --from=build /repo /app

WORKDIR /app/apps/${APP_NAME}
ENV NODE_ENV=production
EXPOSE 3000

CMD ["pnpm", "exec", "next", "start", "-H", "0.0.0.0", "-p", "3000"]