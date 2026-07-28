#!/usr/bin/env bash
# Обследование сервера. Ничего не меняет — только смотрит и рассказывает.
# Запускать первым, до всего остального:  ./deploy/inspect.sh

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

say "${C_BOLD}Обследование сервера${C_RESET}  $(date '+%d.%m.%Y %H:%M')"
say "${C_DIM}Ничего не запускаю и не меняю, только собираю картину.${C_RESET}"

# --------------------------------------------------------------- машина
head1 "Машина"
dim "$(uname -srm)"
[ -r /etc/os-release ] && dim "$(. /etc/os-release && echo "$PRETTY_NAME")"
dim "CPU: $(cpu_count) ядер, загрузка за минуту: $(load_1min)"
dim "Память: $(mem_free_mb) МБ свободно из $(mem_total_mb) МБ"
dim "Диск в $PROJECT_DIR: $(disk_free_mb) МБ свободно"

# --------------------------------------------------------------- docker
head1 "Docker"
if docker_ready; then
  ok "docker работает — $(docker --version | cut -d, -f1)"
  if docker compose version >/dev/null 2>&1; then
    ok "docker compose v2: $(docker compose version --short 2>/dev/null)"
  elif have docker-compose; then
    warn "только старый docker-compose v1 — работать будет, но лучше обновиться"
  else
    bad "compose не найден: apt install docker-compose-plugin"
  fi
  dim "образы занимают: $(docker system df --format '{{.Size}}' 2>/dev/null | head -1)"
else
  bad "docker недоступен (не установлен, не запущен или нет прав у пользователя)"
  dim "права: sudo usermod -aG docker \$USER, потом перезайти по ssh"
fi

# --------------------------------------------------------------- соседи
head1 "Что уже крутится"
FOUND_NEIGHBOURS=0
if docker_ready; then
  containers=$(docker ps --format '{{.Names}}\t{{.Status}}\t{{.Ports}}' 2>/dev/null)
  if [ -n "$containers" ]; then
    FOUND_NEIGHBOURS=1
    say "  ${C_DIM}контейнеры:${C_RESET}"
    printf '%s\n' "$containers" | while IFS=$'\t' read -r name status ports; do
      printf '    %-26s %-22s %s\n' "$name" "${status:0:22}" "${ports:0:44}"
    done
  else
    dim "запущенных контейнеров нет"
  fi
fi

if have systemctl; then
  units=$(systemctl list-units --type=service --state=running --no-legend --no-pager 2>/dev/null \
          | awk '{print $1}' \
          | grep -viE '^(systemd|dbus|cron|ssh|rsyslog|networkd|resolved|udev|polkit|getty|snapd|unattended|accounts|multipathd|irqbalance|chrony|ufw|docker|containerd|walinuxagent|qemu|serial)' \
          | head -20)
  if [ -n "$units" ]; then
    FOUND_NEIGHBOURS=1
    say "  ${C_DIM}systemd-сервисы (кроме системных):${C_RESET}"
    printf '%s\n' "$units" | sed 's/^/    /'
  fi
fi
[ "$FOUND_NEIGHBOURS" = "0" ] && dim "соседних сервисов не видно — машина, похоже, свободна"

# --------------------------------------------------------------- порты
head1 "Занятые порты"
if have ss; then
  ss -Hltnp 2>/dev/null | awk '{split($4,a,":"); print a[length(a)]"\t"$6}' \
    | sort -un | head -25 | sed 's/^/    /' || dim "не удалось прочитать"
else
  warn "ss не установлен: apt install iproute2"
fi

# --------------------------------------------------------------- прокси
head1 "Обратный прокси"
PROXY="нет"
if docker ps --format '{{.Image}}' 2>/dev/null | grep -qi traefik; then PROXY="traefik (в docker)"
elif docker ps --format '{{.Image}}' 2>/dev/null | grep -qi 'nginx\|caddy'; then PROXY="nginx/caddy (в docker)"
elif have nginx && (systemctl is-active nginx >/dev/null 2>&1); then PROXY="nginx (на хосте)"
elif have caddy && (systemctl is-active caddy >/dev/null 2>&1); then PROXY="caddy (на хосте)"
fi
dim "$PROXY"
if [ -d /etc/nginx/sites-enabled ]; then
  say "  ${C_DIM}сайты nginx:${C_RESET}"
  ls -1 /etc/nginx/sites-enabled 2>/dev/null | sed 's/^/    /'
fi

# --------------------------------------------------------------- соседние проекты
head1 "Соседние проекты и их .env"
SEARCH_ROOTS=("$HOME" "/opt" "/srv" "/var/www" "$(dirname "$PROJECT_DIR")")
declare -A seen_dirs=()
for root in "${SEARCH_ROOTS[@]}"; do
  [ -d "$root" ] || continue
  while IFS= read -r envfile; do
    d="$(dirname "$envfile")"
    [ "$d" = "$PROJECT_DIR" ] && continue
    [ -n "${seen_dirs[$d]:-}" ] && continue
    seen_dirs[$d]=1
    keys=$(grep -oE '^[A-Z0-9_]+' "$envfile" 2>/dev/null | sort -u | tr '\n' ' ')
    printf '    %s\n' "$d"
    printf '      %sключи: %s%s\n' "$C_DIM" "${keys:0:150}" "$C_RESET"
  done < <(find "$root" -maxdepth 3 -name '.env' -type f 2>/dev/null | head -12)
done
[ ${#seen_dirs[@]} -eq 0 ] && dim "файлов .env у соседей не нашлось — креды заполним вручную"

# --------------------------------------------------------------- вывод
head1 "Итог"
FREE_MEM=$(mem_free_mb); FREE_DISK=$(disk_free_mb)
say "  Самобранке нужно примерно 900 МБ памяти на сборку, 200 МБ в работе"
say "  и около 2 ГБ диска под образ, базу и картинки."
say ""
if [ "$FREE_MEM" -ge 900 ]; then ok "памяти хватает ($FREE_MEM МБ)"
elif [ "$FREE_MEM" -ge 400 ]; then warn "памяти впритык ($FREE_MEM МБ) — собирайте образ, когда соседи не под нагрузкой"
else bad "памяти мало ($FREE_MEM МБ) — сборка может уронить соседей"; fi

if [ "$FREE_DISK" -ge 2500 ]; then ok "диска хватает ($FREE_DISK МБ)"
elif [ "$FREE_DISK" -ge 1200 ]; then warn "диска впритык ($FREE_DISK МБ) — сначала docker system prune"
else bad "диска мало ($FREE_DISK МБ)"; fi

PORT=$(pick_free_port "${WEB_PORT:-8080}" || echo "")
[ -n "$PORT" ] && ok "свободный порт для сайта: $PORT" || bad "не нашёл свободный порт в диапазоне"

say ""
say "  Дальше: ./deploy/adopt_env.sh — соберёт .env, забрав что можно у соседей"
