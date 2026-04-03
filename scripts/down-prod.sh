#!/usr/bin/env bash
set -e

docker compose \
  --env-file ./env/prod.env \
  -p yuviron-prod \
  -f ./infra/compose.base.yml \
  -f ./infra/compose.prod.yml \
  down