#!/bin/bash

set -e

echo "=============================="
echo " Disk cleanup started"
echo "=============================="

echo ""
echo "1. Docker builder cache..."
docker builder prune -a -f

echo ""
echo "2. Dangling images..."
docker image prune -f

echo ""
echo "3. Unused images..."
docker image prune -a -f

echo ""
echo "4. Stopped containers..."
docker container prune -f

echo ""
echo "5. Unused networks..."
docker network prune -f

echo ""
echo "6. Docker logs cleanup..."
sudo find /var/lib/docker/containers/ -name "*.log" -type f -exec truncate -s 0 {} \;

echo ""
echo "7. Journal logs cleanup..."
sudo journalctl --vacuum-size=100M

echo ""
echo "8. Apt cache cleanup..."
sudo apt-get clean

echo ""
echo "9. Temp files..."
sudo rm -rf /tmp/*
sudo rm -rf /var/tmp/*

echo ""
echo "=============================="
echo " Disk usage after cleanup:"
echo "=============================="
df -h

echo ""
echo " Cleanup completed"
