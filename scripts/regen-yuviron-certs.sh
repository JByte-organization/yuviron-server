#!/usr/bin/env bash
set -euo pipefail

CERTS_DIR="/opt/yuviron-server/certs"
TMP_DIR="$(mktemp -d)"
CAROOT="$(mkcert -CAROOT)"
ROOT_CA_SRC="${CAROOT}/rootCA.pem"
ROOT_CA_DST="${HOME}/rootCA.crt"

CERT_FILE="yuviron-cert.pem"
KEY_FILE="yuviron-key.pem"

DOMAINS=(
  "yuviron.com"
  "api.yuviron.com"
  "dev.yuviron.com"
  "api-dev.yuviron.com"
  "*.yuviron.com"
)

cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

echo "==> Проверка mkcert..."
if ! command -v mkcert >/dev/null 2>&1; then
  echo "Ошибка: mkcert не установлен или не найден в PATH."
  exit 1
fi

echo "==> Проверка root CA..."
if [[ ! -f "$ROOT_CA_SRC" ]]; then
  echo "Ошибка: не найден файл $ROOT_CA_SRC"
  echo "Сначала выполни: mkcert -install"
  exit 1
fi

echo "==> Генерация нового сертификата через mkcert от текущего пользователя..."
mkcert \
  -cert-file "${TMP_DIR}/${CERT_FILE}" \
  -key-file "${TMP_DIR}/${KEY_FILE}" \
  "${DOMAINS[@]}"

echo "==> Очистка папки с сертификатами: $CERTS_DIR"
sudo mkdir -p "$CERTS_DIR"
sudo rm -rf "${CERTS_DIR:?}/"*

echo "==> Копирование сертификатов в $CERTS_DIR"
sudo install -m 644 "${TMP_DIR}/${CERT_FILE}" "${CERTS_DIR}/${CERT_FILE}"
sudo install -m 600 "${TMP_DIR}/${KEY_FILE}" "${CERTS_DIR}/${KEY_FILE}"

echo "==> Копирование rootCA.pem -> ~/rootCA.crt"
cp "$ROOT_CA_SRC" "$ROOT_CA_DST"

echo
echo "Готово."
echo "Сертификаты созданы:"
echo "  CRT: ${CERTS_DIR}/${CERT_FILE}"
echo "  KEY: ${CERTS_DIR}/${KEY_FILE}"
echo "Root CA скопирован в:"
echo "  ${ROOT_CA_DST}"
echo
echo 'pscp -i "D:\graduate work\keys_vm\private_key.ppk" nf@26.207.242.218:/home/nf/rootCA.crt C:\Users\nf\Desktop'
