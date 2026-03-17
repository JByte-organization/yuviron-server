#!/usr/bin/env bash

set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
    exec sudo "$0" "$@"
fi

export DEBIAN_FRONTEND=noninteractive

check_dns() {
    if ! getent hosts download.docker.com >/dev/null 2>&1; then
        echo "Ошибка: не работает DNS."
        echo "Сервер не может резолвить download.docker.com"
        echo
        echo "Проверь сеть и /etc/resolv.conf"
        exit 1
    fi
}

install_docker() {
    if command -v docker >/dev/null 2>&1; then
        return
    fi

    echo "Установка Docker..."

    apt update
    apt install -y ca-certificates curl

    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc

    cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Signed-By: /etc/apt/keyrings/docker.asc
EOF

    apt update
    apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

    getent group docker >/dev/null || groupadd docker
}

collect_users() {
    USERS=("Никого")

    while IFS=: read -r name _ uid _ _ home shell; do
        if [[ "$home" == /home/* ]] && [[ "$shell" != */nologin ]] && [[ "$shell" != */false ]]; then
            USERS+=("$name")
        fi
    done < /etc/passwd
}

draw_menu() {
    clear
    echo "Выберите пользователей для группы docker"
    echo

    for i in "${!USERS[@]}"; do
        prefix="  "
        mark="[ ]"

        if [[ "$i" -eq "$CURSOR" ]]; then
            prefix="> "
        fi

        if [[ "${SELECTED[$i]:-0}" -eq 1 ]]; then
            mark="[x]"
        fi

        echo "${prefix}${mark} ${USERS[$i]}"
    done

    echo
    echo "↑ ↓ - перемещение"
    echo "Space - выбрать"
    echo "Enter - подтвердить"
}

toggle_item() {
    local index="$1"

    if [[ "$index" -eq 0 ]]; then
        for i in "${!USERS[@]}"; do
            SELECTED[$i]=0
        done
        SELECTED[0]=1
        return
    fi

    SELECTED[0]=0

    if [[ "${SELECTED[$index]:-0}" -eq 1 ]]; then
        SELECTED[$index]=0
    else
        SELECTED[$index]=1
    fi
}

read_key() {
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

    trap 'tput cnorm 2>/dev/null || true; stty echo 2>/dev/null || true' EXIT
    tput civis 2>/dev/null || true

    while true; do
        draw_menu

        if ! key="$(read_key)"; then
            break
        fi

        case "$key" in
            $'\x1b[A')
                CURSOR=$((CURSOR - 1))
                if [[ "$CURSOR" -lt 0 ]]; then
                    CURSOR=$((${#USERS[@]} - 1))
                fi
                ;;
            $'\x1b[B')
                CURSOR=$((CURSOR + 1))
                if [[ "$CURSOR" -ge "${#USERS[@]}" ]]; then
                    CURSOR=0
                fi
                ;;
            " ")
                toggle_item "$CURSOR"
                ;;
            "")
                break
                ;;
        esac
    done

    tput cnorm 2>/dev/null || true
    clear
    apply_selection
}

check_dns
install_docker
collect_users
main_menu