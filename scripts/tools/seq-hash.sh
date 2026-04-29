#!/usr/bin/env bash

set -e

echo "Enter password:"
read -s PASSWORD

echo
echo "Repeat password:"
read -s PASSWORD_CONFIRM

echo

if [[ "$PASSWORD" != "$PASSWORD_CONFIRM" ]]; then
  echo "❌ Passwords do not match"
  exit 1
fi

if [[ -z "$PASSWORD" ]]; then
  echo "❌ Password cannot be empty"
  exit 1
fi

echo "🔐 Generating hash..."

HASH=$(printf '%s' "$PASSWORD" | docker run --rm -i datalust/seq:latest config hash)

echo
echo "✅ Hash generated:"
echo "$HASH"
