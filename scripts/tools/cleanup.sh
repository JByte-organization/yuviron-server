#!/bin/bash
set -e

AUTO_YES=false

while getopts "y" opt; do
  case ${opt} in
    y )
      AUTO_YES=true
      ;;
    \? )
      echo "Usage: $0 [-y]"
      exit 1
      ;;
  esac
done

confirm() {
  if [ "$AUTO_YES" = true ]; then
    return 0
  fi

  read -rp "$1 (Y/n): " response
  case "$response" in
    [yY][eE][sS]|[yY]|"" ) return 0 ;;
    * ) return 1 ;;
  esac
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "Requesting sudo access..."
sudo -v

while true; do
  sudo -n true
  sleep 60
  kill -0 "$$" || exit
done 2>/dev/null &

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
echo "Apt cache cleaned"

echo ""
echo "9. Temp files cleanup..."
sudo find /tmp /var/tmp -mindepth 1 -maxdepth 1 -exec rm -rf {} +
echo "Temp files cleaned: /tmp and /var/tmp"

echo ""
echo "10. Python __pycache__ cleanup..."
PYCACHE_COUNT="$(find "$PROJECT_ROOT" -type d -name "__pycache__" | wc -l)"

if [ "$PYCACHE_COUNT" -gt 0 ]; then
    find "$PROJECT_ROOT" \
      -type d \
      -name "__pycache__" \
      -prune \
      -exec rm -rf {} +

    echo "Removed __pycache__ directories: $PYCACHE_COUNT"
else
    echo "No __pycache__ directories found under $PROJECT_ROOT"
fi

echo ""
echo "11. Removing generated folder..."
if [ -d "$PROJECT_ROOT/generated" ]; then
    echo "WARNING: removing generated will require re-running scripts/init.py before next up/preflight."
    if confirm "Remove $PROJECT_ROOT/generated?"; then
        rm -rf "$PROJECT_ROOT/generated"
        echo "Removed $PROJECT_ROOT/generated"
    else
        echo "Skipped $PROJECT_ROOT/generated"
    fi
else
    echo "$PROJECT_ROOT/generated not found"
fi

echo ""
echo "12. Removing certs folder..."
if [ -d "$PROJECT_ROOT/certs" ]; then
    echo "WARNING: removing certs may break nginx startup until certificates are regenerated."
    if confirm "Remove $PROJECT_ROOT/certs?"; then
        rm -rf "$PROJECT_ROOT/certs"
        echo "Removed $PROJECT_ROOT/certs"
    else
        echo "Skipped $PROJECT_ROOT/certs"
    fi
else
    echo "$PROJECT_ROOT/certs not found"
fi

echo ""
echo "13. Removing .tmp folder..."
if [ -d "$PROJECT_ROOT/.tmp" ]; then
    echo "WARNING: removing .tmp may clear local caches or temporary session data."
    if confirm "Remove $PROJECT_ROOT/.tmp?"; then
        rm -rf "$PROJECT_ROOT/.tmp"
        echo "Removed $PROJECT_ROOT/.tmp"
    else
        echo "Skipped $PROJECT_ROOT/.tmp"
    fi
else
    echo "$PROJECT_ROOT/.tmp not found"
fi

echo ""
echo "=============================="
echo " Disk usage after cleanup:"
echo "=============================="
df -h

echo ""
echo "Cleanup completed"