#!/usr/bin/env bash
# Общие функции для всех скриптов деплоя. Отдельно не запускается.

set -uo pipefail

C_RESET=$'\033[0m'; C_DIM=$'\033[2m'; C_RED=$'\033[31m'
C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_BOLD=$'\033[1m'

say()   { printf '%s\n' "$*"; }
head1() { printf '\n%s%s%s\n' "$C_BOLD" "$*" "$C_RESET"; }
ok()    { printf '  %s✓%s %s\n' "$C_GREEN" "$C_RESET" "$*"; }
warn()  { printf '  %s!%s %s\n' "$C_YELLOW" "$C_RESET" "$*"; }
bad()   { printf '  %s✗%s %s\n' "$C_RED" "$C_RESET" "$*"; }
dim()   { printf '  %s%s%s\n' "$C_DIM" "$*" "$C_RESET"; }

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="$PROJECT_DIR/deploy/.state"
mkdir -p "$STATE_DIR"

# --- docker -----------------------------------------------------------------

have() { command -v "$1" >/dev/null 2>&1; }

DOCKER_CMD=""
compose() {
  if [ -z "$DOCKER_CMD" ]; then
    if docker compose version >/dev/null 2>&1; then DOCKER_CMD="docker compose"
    elif have docker-compose;               then DOCKER_CMD="docker-compose"
    else return 127; fi
  fi
  # shellcheck disable=SC2086
  (cd "$PROJECT_DIR" && $DOCKER_CMD "$@")
}

docker_ready() { have docker && docker info >/dev/null 2>&1; }

# --- ресурсы ----------------------------------------------------------------

mem_free_mb()  { awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo 2>/dev/null || echo 0; }
mem_total_mb() { awk '/MemTotal/     {print int($2/1024)}' /proc/meminfo 2>/dev/null || echo 0; }
disk_free_mb() { df -Pm "$PROJECT_DIR" 2>/dev/null | awk 'NR==2 {print $4}' || echo 0; }
cpu_count()    { nproc 2>/dev/null || echo 1; }
load_1min()    { awk '{print $1}' /proc/loadavg 2>/dev/null || echo 0; }

# Занят ли порт хоть кем-нибудь на хосте
port_busy() {
  local port="$1"
  if have ss; then ss -Hltn "sport = :$port" 2>/dev/null | grep -q . && return 0
  elif have netstat; then netstat -ltn 2>/dev/null | awk '{print $4}' | grep -qE "[:.]$port$" && return 0
  fi
  # порт может быть проброшен только внутрь docker — проверим и там
  if docker_ready; then
    docker ps --format '{{.Ports}}' 2>/dev/null | grep -qE "(^|[:, ])$port->" && return 0
  fi
  return 1
}

pick_free_port() {
  # раздельные local: в одной строке bash раскрывает все слова до присваивания,
  # и "$start" во втором выражении оказывается ещё не определён
  local start="${1:-8080}"
  local port="$start"
  for _ in $(seq 1 60); do
    port_busy "$port" || { echo "$port"; return 0; }
    port=$((port + 1))
  done
  return 1
}

# --- прочее -----------------------------------------------------------------

confirm() {
  local prompt="$1"
  if [ "${ASSUME_YES:-0}" = "1" ]; then return 0; fi
  read -r -p "$prompt [y/N] " answer
  [[ "$answer" =~ ^[Yy]$ ]]
}

http_code() {
  curl -s -o /dev/null -w '%{http_code}' --max-time "${2:-6}" "$1" 2>/dev/null || echo "000"
}

# Записать ключ в .env-файл: заменить существующую строку или дописать в конец.
# Значение передаётся через окружение, чтобы awk не трогал спецсимволы в ключах.
set_env_var() {
  local file="$1" key="$2" value="$3" tmp
  [ -f "$file" ] || touch "$file"
  tmp="$(mktemp)"
  SB_VALUE="$value" awk -v k="$key" '
    BEGIN { done = 0 }
    $0 ~ "^[[:space:]]*"k"=" { print k "=" ENVIRON["SB_VALUE"]; done = 1; next }
    { print }
    END { if (!done) print k "=" ENVIRON["SB_VALUE"] }
  ' "$file" > "$tmp" && mv "$tmp" "$file"
  chmod 600 "$file"
}

get_env_var() {
  local file="$1" key="$2"
  [ -f "$file" ] || return 1
  grep -m1 -E "^[[:space:]]*${key}=" "$file" 2>/dev/null | sed -E "s/^[^=]*=//"
}

gen_secret() {
  openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'
}

gen_password() {
  openssl rand -base64 12 2>/dev/null | tr -d '/+=' | cut -c1-10 || echo "smena$RANDOM"
}
