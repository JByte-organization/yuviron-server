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

FROM nginx:alpine AS runtime
ARG APP_NAME

COPY --from=build /repo/apps/${APP_NAME}/dist /usr/share/nginx/html
RUN printf 'server {\n    listen 3000;\n    server_name _;\n\n    root /usr/share/nginx/html;\n    index index.html;\n\n    location / {\n        try_files $uri $uri/ /index.html;\n    }\n}\n' > /etc/nginx/conf.d/default.conf

EXPOSE 3000
CMD ["nginx", "-g", "daemon off;"]
