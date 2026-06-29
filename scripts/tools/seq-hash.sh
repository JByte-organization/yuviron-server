#!/usr/bin/env bash
# =============================================================================
# scripts/tools/seq-hash.sh — Генерация хэша пароля для Seq.
#
# Seq (структурированный логгер) хранит пароль администратора в формате
# собственного хэша, который отличается от bcrypt/MD5/SHA.
#
# Этот хэш нужно:
#   1. Сгенерировать этим скриптом (или tools seq-hash через CLI)
#   2. Записать в SEQ_FIRSTRUN_ADMINPASSWORDHASH в env/prod.env
#
# ВАЖНО: хэш применяется только при первом запуске (первоначальной инициализации).
# Если Seq уже запущен — менять пароль нужно через его Web UI.
#
# Запуск: ./scripts/tools/seq-hash.sh
# Альтернатива: ./scripts/cli.py tools seq-hash
# =============================================================================

set -e

echo "Enter password:"
read -rs PASSWORD  # -s не выводит символы (секретный ввод)

echo
echo "Repeat password:"
read -rs PASSWORD_CONFIRM

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

# Запускаем утилиту хэширования внутри официального Seq-контейнера.
# printf используется вместо echo чтобы не добавлять лишний \n в конце.
HASH=$(printf '%s' "$PASSWORD" | docker run --rm -i datalust/seq:2025.2@sha256:868a12e93ec0b8c993767a7dd4cd6c8ebc441511c79e4cfac66c911f62d4db65 config hash)

echo
echo "✅ Hash generated:"
echo "$HASH"
