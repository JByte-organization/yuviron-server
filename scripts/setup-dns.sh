#!/bin/bash

echo "=============================="
echo " Yuviron Local DNS Setup"
echo "=============================="

# Проверка root
if [ "$EUID" -ne 0 ]; then
  echo "Запусти скрипт через sudo"
  exit
fi

echo ""
echo "Определяем IP адрес..."

AUTO_IP=$(hostname -I | awk '{print $1}')

echo "Найден IP: $AUTO_IP"
echo ""

read -p "Введите IP сервера (Enter чтобы использовать $AUTO_IP): " USER_IP

IP=${USER_IP:-$AUTO_IP}

echo ""
echo "Будет использован IP: $IP"
echo ""

echo "Устанавливаем dnsmasq..."

apt update -y
apt install dnsmasq -y

echo ""
echo "Создаём конфигурацию DNS..."

cat > /etc/dnsmasq.d/yuviron.conf <<EOF
listen-address=127.0.0.1,$IP
bind-interfaces

server=8.8.8.8
server=1.1.1.1

address=/.yuviron.com/$IP
EOF

echo ""
echo "Перезапускаем DNS..."

systemctl restart dnsmasq
systemctl enable dnsmasq

echo ""
echo "Проверяем конфигурацию..."

dnsmasq --test

echo ""
echo "Тест DNS..."

apt install dnsutils -y >/dev/null 2>&1

dig yuviron.com @$IP +short

echo ""
echo "=============================="
echo " DNS успешно настроен"
echo "=============================="
echo ""
echo "Теперь пользователи должны указать DNS:"
echo ""
echo "DNS server: $IP"
echo ""
echo "После этого будут работать домены:"
echo ""
echo "yuviron.com"
echo "api.yuviron.com"
echo "anything.yuviron.com"
echo ""
