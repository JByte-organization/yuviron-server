#!/usr/bin/env bash
set -e

docker compose \
  --env-file ./env/dev.env \
  -p yuviron-dev \
  -f ./infra/compose.base.yml \
  -f ./infra/compose.dev.yml \
  up -d --build --remove-orphans