#!/usr/bin/env bash
set -euo pipefail

CERTS_DIR="../certs"
TMP_DIR="$(mktemp -d)"

CERT_FILE="yuviron-cert.pem"
KEY_FILE="yuviron-key.pem"

DOMAINS=(
  "yuviron.com"
  "api.yuviron.com"
  "dev.yuviron.com"
  "dev-api.yuviron.com"
  "*.yuviron.com"
)

cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

echo "==> Проверка mkcert..."

if ! command -v mkcert >/dev/null 2>&1; then
  echo "mkcert не найден. Устанавливаем..."

  sudo apt update -y
  sudo apt install -y mkcert libnss3-tools

  echo "==> Установка локального root CA..."
  mkcert -install
fi

CAROOT="$(mkcert -CAROOT)"
ROOT_CA_SRC="${CAROOT}/rootCA.pem"
ROOT_CA_DST="${HOME}/rootCA.crt"

echo "==> Проверка root CA..."

if [[ ! -f "$ROOT_CA_SRC" ]]; then
  echo "==> Root CA не найден. Устанавливаем..."
  mkcert -install
fi

echo "==> Генерация нового сертификата..."

mkcert \
  -cert-file "${TMP_DIR}/${CERT_FILE}" \
  -key-file "${TMP_DIR}/${KEY_FILE}" \
  "${DOMAINS[@]}"

echo "==> Очистка папки сертификатов: $CERTS_DIR"

sudo mkdir -p "$CERTS_DIR"
sudo rm -rf "${CERTS_DIR:?}/"*

echo "==> Копирование сертификатов"

sudo install -m 644 "${TMP_DIR}/${CERT_FILE}" "${CERTS_DIR}/${CERT_FILE}"
sudo install -m 600 "${TMP_DIR}/${KEY_FILE}" "${CERTS_DIR}/${KEY_FILE}"

echo "==> Копирование root CA -> ~/rootCA.crt"

cp "$ROOT_CA_SRC" "$ROOT_CA_DST"

echo
echo "Готово."
echo
echo "CRT:"
echo "  ${CERTS_DIR}/${CERT_FILE}"

echo "KEY:"
echo "  ${CERTS_DIR}/${KEY_FILE}"

echo
echo "Root CA:"
echo "  ${ROOT_CA_DST}"

echo
echo 'Скопировать CA на Windows:'
echo 'pscp -i "{path_to_key}" nf@{ip}:/{path} C:\Users\nf\Desktop'
