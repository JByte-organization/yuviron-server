#!/usr/bin/env bash
# =============================================================================
# scripts/tools/docker_install.sh — Установка Docker CE на Ubuntu.
#
# Что делает:
#   1. Проверяет DNS (нужен для download.docker.com)
#   2. Устанавливает Docker CE, CLI, containerd, buildx и compose plugin
#   3. Создаёт группу docker если нет
#   4. Показывает интерактивное TUI-меню для выбора пользователей
#      которых нужно добавить в группу docker (без sudo)
#
# Требует root (перезапускается через sudo если нужно).
# Работает только на Ubuntu (использует /etc/os-release для кодового имени).
#
# Запуск: ./scripts/tools/docker_install.sh
# Альтернатива: ./scripts/cli.py tools docker-install
# =============================================================================

set -euo pipefail

# Если запущен не от root — перезапустить через sudo
if [[ "${EUID}" -ne 0 ]]; then
    exec sudo "$0" "$@"
fi

export DEBIAN_FRONTEND=noninteractive   # без интерактивных вопросов apt

check_dns() {
    # Проверяем что DNS работает перед попыткой скачать Docker
    if ! getent hosts download.docker.com >/dev/null 2>&1; then
        echo "Ошибка: не работает DNS."
        echo "Сервер не может резолвить download.docker.com"
        echo
        echo "Проверь сеть и /etc/resolv.conf"
        exit 1
    fi
}

install_docker() {
    # Пропустить установку если Docker уже установлен
    if command -v docker >/dev/null 2>&1; then
        return
    fi

    echo "Установка Docker..."

    apt update
    apt install -y ca-certificates curl

    # Создаём папку для GPG-ключей apt
    install -m 0755 -d /etc/apt/keyrings
    # Скачиваем GPG-ключ Docker и сохраняем как .asc (ASCII-armor PGP)
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc

    # Добавляем официальный репозиторий Docker для текущей версии Ubuntu
    # UBUNTU_CODENAME — например "jammy" для 22.04, "noble" для 24.04
    cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Signed-By: /etc/apt/keyrings/docker.asc
EOF

    apt update
    # Устанавливаем Docker CE + Compose plugin + Buildx
    apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

    # Создаём группу docker (если не существует) для управления без sudo
    getent group docker >/dev/null || groupadd docker
}

collect_users() {
    # Собираем список "живых" пользователей: те у кого домашняя папка в /home/
    # и у кого shell не /nologin и не /false (не системные аккаунты).
    # Первый элемент USERS[0] = "Никого" (для отмены без выбора пользователей).
    USERS=("Никого")

    while IFS=: read -r name _ _ _ _ home shell; do
        if [[ "$home" == /home/* ]] && [[ "$shell" != */nologin ]] && [[ "$shell" != */false ]]; then
            USERS+=("$name")
        fi
    done < /etc/passwd
}

draw_menu() {
    # Отрисовываем TUI-меню с чекбоксами в терминале
    clear
    echo "Выберите пользователей для группы docker"
    echo

    for i in "${!USERS[@]}"; do
        prefix="  "
        mark="[ ]"

        if [[ "$i" -eq "$CURSOR" ]]; then
            prefix="> "   # текущая позиция курсора
        fi

        if [[ "${SELECTED[$i]:-0}" -eq 1 ]]; then
            mark="[x]"   # выбран
        fi

        echo "${prefix}${mark} ${USERS[$i]}"
    done

    echo
    echo "↑ ↓ - перемещение"
    echo "Space - выбрать"
    echo "Enter - подтвердить"
}

toggle_item() {
    # Переключить выделение элемента. Если выбран "Никого" (index 0) — снять всё.
    local index="$1"

    if [[ "$index" -eq 0 ]]; then
        # Выбор "Никого" снимает все другие отметки
        for i in "${!USERS[@]}"; do
            SELECTED[$i]=0
        done
        SELECTED[0]=1
        return
    fi

    SELECTED[0]=0   # снимаем "Никого" при выборе любого пользователя

    if [[ "${SELECTED[$index]:-0}" -eq 1 ]]; then
        SELECTED[$index]=0
    else
        SELECTED[$index]=1
    fi
}

read_key() {
    # Читаем нажатие клавиши (включая escape-последовательности для стрелок).
    # Escape-последовательности: \x1b[ + A/B = Up/Down
    local key key2 key3

    IFS= read -rsn1 key < /dev/tty || return 1

    if [[ "$key" == $'\x1b' ]]; then
        IFS= read -rsn1 key2 < /dev/tty || true
        IFS= read -rsn1 key3 < /dev/tty || true
        printf '%s%s%s' "$key" "$key2" "$key3"
    else
        printf '%s' "$key"
    fi
}

apply_selection() {
    # Применить выбранных пользователей: добавить их в группу docker через usermod.
    # После добавления пользователи должны перелогиниться чтобы изменения вступили в силу.
    local added=()

    if [[ "${SELECTED[0]:-0}" -eq 1 ]]; then
        echo
        echo "Никого не добавляем в группу docker."
        return
    fi

    for i in "${!USERS[@]}"; do
        if [[ "$i" -eq 0 ]]; then
            continue
        fi

        if [[ "${SELECTED[$i]:-0}" -eq 1 ]]; then
            usermod -aG docker "${USERS[$i]}"
            added+=("${USERS[$i]}")
        fi
    done

    echo
    if [[ "${#added[@]}" -eq 0 ]]; then
        echo "Никто не выбран."
    else
        echo "Добавлены в группу docker: ${added[*]}"
        echo "Пользователям нужно перелогиниться."
    fi
}

main_menu() {
    CURSOR=0
    declare -gA SELECTED=()

    # При выходе — восстанавливаем курсор и режим эха терминала
    trap 'tput cnorm 2>/dev/null || true; stty echo 2>/dev/null || true' EXIT
    tput civis 2>/dev/null || true   # спрятать курсор во время TUI

    while true; do
        draw_menu

        if ! key="$(read_key)"; then
            break
        fi

        case "$key" in
            $'\x1b[A')   # стрелка вверх
                CURSOR=$((CURSOR - 1))
                if [[ "$CURSOR" -lt 0 ]]; then
                    CURSOR=$((${#USERS[@]} - 1))   # wrap around в конец списка
                fi
                ;;
            $'\x1b[B')   # стрелка вниз
                CURSOR=$((CURSOR + 1))
                if [[ "$CURSOR" -ge "${#USERS[@]}" ]]; then
                    CURSOR=0   # wrap around в начало
                fi
                ;;
            " ")   # пробел — выбрать/снять
                toggle_item "$CURSOR"
                ;;
            "")   # Enter — подтвердить
                break
                ;;
        esac
    done

    tput cnorm 2>/dev/null || true   # восстановить курсор
    clear
    apply_selection
}

check_dns
install_docker
collect_users
main_menu