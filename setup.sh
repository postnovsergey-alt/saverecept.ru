#!/usr/bin/env bash
# Самобранка — установка одной командой.
#
#   ./setup.sh              поднять на этой машине (для разработки)
#   ./setup.sh --server     развернуть в docker ЗДЕСЬ (запускать на сервере)
#   ./setup.sh --remote     доставить на сервер по SSH и развернуть там
#   ./setup.sh --update     пересобрать уже развёрнутое
#
# Человека спрашиваю ровно об одном: о ключах, которые выдают Google и Telegram
# после входа под вашей учёткой. Их взять программно нельзя. Всё остальное —
# зависимости, пароли, порты, конфиги, проверки, откат — скрипт делает сам,
# и ответы запоминает, так что второй раз не спросит.

source "$(dirname "${BASH_SOURCE[0]}")/deploy/lib.sh"
source "$(dirname "${BASH_SOURCE[0]}")/deploy/secrets.sh"

MODE="local"
SKIP_DEPS=0
for arg in "$@"; do
  case "$arg" in
    --server) MODE="server" ;;
    --remote) MODE="remote" ;;
    --update) MODE="update" ;;
    --yes)    ASSUME_YES=1 ;;
    --skip-deps) SKIP_DEPS=1 ;;
    --help|-h)
      sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
  esac
done

cd "$PROJECT_DIR"
ENV_FILE="$PROJECT_DIR/.env"

say "${C_BOLD}Самобранка${C_RESET}  ${C_DIM}режим: $MODE${C_RESET}"

# ============================================================================
# Общая часть: секреты и .env
# ============================================================================

prepare_env() {
  collect_secrets
  load_secrets

  head1 "Собираю конфигурацию"

  if [ ! -f "$ENV_FILE" ]; then
    cp "$PROJECT_DIR/.env.example" "$ENV_FILE"
    ok "создан .env из шаблона"
  else
    ok ".env уже есть, дополню недостающее"
  fi

  # то, что генерируется само
  if [ -z "$(get_env_var "$ENV_FILE" SECRET_KEY)" ]; then
    set_env_var "$ENV_FILE" SECRET_KEY "$(gen_secret)"
    ok "SECRET_KEY сгенерирован"
  fi

  # порт: берём из .env, если он там свободен, иначе подбираем
  local port
  port="$(get_env_var "$ENV_FILE" WEB_PORT)"
  port="${port:-8080}"
  if port_busy "$port" && ! docker ps --format '{{.Names}}\t{{.Ports}}' 2>/dev/null | grep -q "samobranka-web.*$port"; then
    local newport
    newport="$(pick_free_port $((port + 1)))" || { bad "нет свободных портов"; exit 1; }
    warn "порт $port занят соседом, беру $newport"
    port="$newport"
  fi
  set_env_var "$ENV_FILE" WEB_PORT "$port"
  SITE_PORT="$port"
  ok "порт: $port"

  # секреты от человека
  [ -n "${SECRET_GEMINI_KEY:-}" ] && set_env_var "$ENV_FILE" LLM_API_KEY "$SECRET_GEMINI_KEY"
  [ -n "${SECRET_TG_TOKEN:-}" ]   && set_env_var "$ENV_FILE" TELEGRAM_BOT_TOKEN "$SECRET_TG_TOKEN"

  # то, что можно позаимствовать у соседних проектов
  if [ "$MODE" != "local" ] && [ -z "$(get_env_var "$ENV_FILE" LLM_API_KEY)" ]; then
    local borrowed
    borrowed="$(borrow_from_neighbors GEMINI_API_KEY GOOGLE_API_KEY GOOGLE_GENAI_API_KEY LLM_API_KEY)"
    if [ -n "$borrowed" ]; then
      set_env_var "$ENV_FILE" LLM_API_KEY "$borrowed"
      ok "ключ LLM взят из .env соседнего проекта"
    fi
  fi

  # публичный адрес
  local base
  if [ -n "${SECRET_DOMAIN:-}" ]; then
    base="${SECRET_DOMAIN}"
    [[ "$base" =~ ^https?:// ]] || base="https://$base"
  elif [ "$MODE" = "local" ]; then
    base="http://localhost:$port"
  else
    local ip; ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
    base="http://${ip:-localhost}:$port"
  fi
  set_env_var "$ENV_FILE" PUBLIC_BASE_URL "${base%/}"
  SITE_URL="${base%/}"
  ok "адрес: $SITE_URL"
}

borrow_from_neighbors() {
  local key f val
  for root in "$HOME" "/opt" "/srv" "/var/www" "$(dirname "$PROJECT_DIR")"; do
    [ -d "$root" ] || continue
    while IFS= read -r f; do
      [ "$(dirname "$f")" = "$PROJECT_DIR" ] && continue
      for key in "$@"; do
        val=$(grep -m1 -E "^[[:space:]]*${key}=" "$f" 2>/dev/null \
              | sed -E 's/^[^=]*=[[:space:]]*//; s/^["'"'"']//; s/["'"'"']$//' | tr -d '\r')
        [ -n "$val" ] && { printf '%s' "$val"; return 0; }
      done
    done < <(find "$root" -maxdepth 3 -name '.env' -type f 2>/dev/null)
  done
  return 0
}

# ============================================================================
# Локальный запуск — для разработки на своей машине
# ============================================================================

run_local() {
  head1 "Готовлю окружение"
  if ! have python3; then
    bad "нет python3. Поставьте его и повторите."
    exit 1
  fi

  if [ ! -d "$PROJECT_DIR/.venv" ]; then
    python3 -m venv "$PROJECT_DIR/.venv"
    ok "создано виртуальное окружение"
  fi
  PY="$PROJECT_DIR/.venv/bin/python"

  if [ "$SKIP_DEPS" = "1" ]; then
    dim "установка зависимостей пропущена (--skip-deps)"
  else
    # обновление самого pip не обязано получаться: за прокси или без сети
    # оно просто зависнет, а работать это не мешает
    "$PY" -m pip install --quiet --disable-pip-version-check \
          --timeout 20 --retries 1 --upgrade pip >/dev/null 2>&1 || true

    if "$PY" -m pip install --quiet --disable-pip-version-check \
             --timeout 30 --retries 2 -r "$PROJECT_DIR/requirements.txt"; then
      ok "зависимости установлены"
    else
      bad "не удалось поставить зависимости"
      dim "чаще всего это сеть или прокси. Повторите, либо, если библиотеки"
      dim "уже стоят, запустите с флагом --skip-deps"
      exit 1
    fi
  fi

  head1 "Проверяю, что код рабочий"
  if "$PY" -m tests.test_parser >/dev/null 2>&1; then ok "разбор рецептов работает"
  else bad "тесты парсера не прошли"; "$PY" -m tests.test_parser | tail -20; exit 1; fi
  if "$PY" -m tests.test_web >/dev/null 2>&1; then ok "сайт собирается"
  else bad "тесты сайта не прошли"; "$PY" -m tests.test_web | tail -20; exit 1; fi

  prepare_env

  if [ -n "$(get_env_var "$ENV_FILE" LLM_API_KEY)" ]; then
    head1 "Проверяю модель"
    "$PY" -m tools.check_llm 2>&1 | tail -4
  fi

  head1 "Наполняю примерами"
  "$PY" -m tools.demo_seed 2>&1 | tail -1

  head1 "Готово"
  say "  Сайт:  ${C_BOLD}$SITE_URL${C_RESET}"
  say "  Демо:  логин ${C_BOLD}demo@saverecept.ru${C_RESET}  пароль ${C_BOLD}demo1234${C_RESET}"
  say "         или зарегистрируйте свой аккаунт: ${C_BOLD}$SITE_URL/register${C_RESET}"
  say ""
  say "  ${C_DIM}Останавливать — Ctrl+C. Правки в коде подхватываются на лету.${C_RESET}"
  say ""

  # открыть браузер, если умеем
  ( sleep 2
    if   have xdg-open; then xdg-open "$SITE_URL" >/dev/null 2>&1
    elif have open;     then open "$SITE_URL" >/dev/null 2>&1
    fi ) &

  # через -m, а не через .venv/bin/uvicorn: так работает и когда venv создан
  # с системными пакетами и своего скрипта в bin нет
  exec "$PY" -m uvicorn app.main:app --host 0.0.0.0 --port "$SITE_PORT" --reload
}

# ============================================================================
# Развёртывание здесь, в докере — запускается на сервере
# ============================================================================

run_server() {
  head1 "Смотрю, что на машине"
  say "  память: $(mem_free_mb) МБ свободно из $(mem_total_mb) МБ"
  say "  диск:   $(disk_free_mb) МБ свободно"
  say "  ядер:   $(cpu_count), загрузка $(load_1min)"

  local neighbours
  neighbours=$(docker ps --format '{{.Names}}' 2>/dev/null | grep -v samobranka | tr '\n' ' ')
  [ -n "$neighbours" ] && say "  соседи: $neighbours" || say "  соседей не видно"

  prepare_env

  head1 "Запускаю деплой"
  say "  ${C_DIM}Дальше работает deploy.sh: проверки, снимок соседей, точка отката,${C_RESET}"
  say "  ${C_DIM}сборка, запуск, сверка соседей. При неудаче откатится сам.${C_RESET}"
  say ""

  if [ "${ASSUME_YES:-0}" = "1" ]; then
    bash "$PROJECT_DIR/deploy/deploy.sh" --yes || return 1
  else
    bash "$PROJECT_DIR/deploy/deploy.sh" || return 1
  fi

  setup_nginx

  head1 "Готово"
  say "  Сайт:  ${C_BOLD}$SITE_URL${C_RESET}"
  say "  ${C_DIM}Регистрация: $SITE_URL/register${C_RESET}"
}

setup_nginx() {
  [ -z "${SECRET_DOMAIN:-}" ] && return 0
  have nginx || return 0
  [ -d /etc/nginx/sites-available ] || return 0

  head1 "Настраиваю nginx для $SECRET_DOMAIN"

  local domain="${SECRET_DOMAIN#http://}"; domain="${domain#https://}"; domain="${domain%%/*}"
  local target="/etc/nginx/sites-available/samobranka"

  if [ -f "$target" ]; then
    ok "конфиг nginx уже есть, не трогаю"
    return 0
  fi

  local tmp; tmp="$(mktemp)"
  sed -e "s/samobranka.example.ru/$domain/g" \
      -e "s|http://127.0.0.1:8080|http://127.0.0.1:$SITE_PORT|g" \
      "$PROJECT_DIR/deploy/nginx.conf.example" > "$tmp"

  if sudo -n true 2>/dev/null; then
    sudo cp "$tmp" "$target"
    sudo ln -sf "$target" /etc/nginx/sites-enabled/samobranka
    # проверка перед перезагрузкой: сломанный конфиг положил бы все сайты
    if sudo nginx -t 2>/dev/null; then
      sudo systemctl reload nginx
      ok "nginx настроен и перезагружен"
      say "  ${C_DIM}Сертификат: sudo certbot --nginx -d $domain${C_RESET}"
    else
      sudo rm -f /etc/nginx/sites-enabled/samobranka
      bad "конфиг nginx не прошёл проверку, откатил — соседние сайты не тронуты"
      sudo nginx -t 2>&1 | sed 's/^/    /'
    fi
  else
    cp "$tmp" "$PROJECT_DIR/deploy/nginx-samobranka.conf"
    warn "нет прав sudo без пароля — конфиг подготовлен, примените вручную:"
    say "    sudo cp deploy/nginx-samobranka.conf /etc/nginx/sites-available/samobranka"
    say "    sudo ln -s /etc/nginx/sites-available/samobranka /etc/nginx/sites-enabled/"
    say "    sudo nginx -t && sudo systemctl reload nginx"
  fi
  rm -f "$tmp"
}

# ============================================================================
# Доставка на сервер по SSH — запускается с ноутбука
# ============================================================================

run_remote() {
  local target_file="$PROJECT_DIR/deploy/.target"
  local target=""

  [ -f "$target_file" ] && target="$(cat "$target_file")"
  if [ -z "$target" ]; then
    head1 "Куда ставим"
    if [ -f "$HOME/.ssh/config" ]; then
      local hosts
      hosts=$(grep -iE '^Host ' "$HOME/.ssh/config" | awk '{print $2}' | grep -v '\*' | tr '\n' ' ')
      [ -n "$hosts" ] && dim "в вашем ~/.ssh/config есть: $hosts"
    fi
    say "  ${C_BOLD}Адрес сервера${C_RESET} ${C_DIM}(например sergey@203.0.113.10 или имя из ssh config)${C_RESET}"
    read -r -p "  > " target
    [ -z "$target" ] && { bad "без адреса никуда не поедем"; exit 1; }
    echo "$target" > "$target_file"
    ok "запомнил, больше не спрошу"
  else
    ok "сервер: $target"
  fi

  head1 "Проверяю связь"
  if ssh -o BatchMode=yes -o ConnectTimeout=8 "$target" 'echo ok' >/dev/null 2>&1; then
    ok "ssh работает без пароля"
  else
    warn "ssh просит пароль или недоступен"
    say "  ${C_DIM}Настроить вход по ключу: ssh-copy-id $target${C_RESET}"
    if ! ssh -o ConnectTimeout=10 "$target" 'echo ok' >/dev/null 2>&1; then
      bad "подключиться не вышло"; exit 1
    fi
  fi

  local remote_dir
  remote_dir="$(ssh "$target" 'echo $HOME')/samobranka"

  head1 "Копирую проект"
  collect_secrets   # спросим здесь, чтобы на сервере уже ничего не спрашивалось
  if have rsync; then
    rsync -az --delete \
      --exclude '.venv' --exclude 'data' --exclude '.git' \
      --exclude '__pycache__' --exclude 'deploy/.state' --exclude '.env' \
      "$PROJECT_DIR/" "$target:$remote_dir/"
    ok "проект скопирован (rsync, данные на сервере не тронуты)"
  else
    ssh "$target" "mkdir -p '$remote_dir'"
    tar czf - --exclude='.venv' --exclude='data' --exclude='.git' \
        --exclude='__pycache__' --exclude='deploy/.state' --exclude='.env' \
        -C "$PROJECT_DIR" . | ssh "$target" "tar xzf - -C '$remote_dir'"
    ok "проект скопирован"
  fi

  # секреты передаём отдельно, файлом с правами 600
  scp -q "$PROJECT_DIR/deploy/.secrets" "$target:$remote_dir/deploy/.secrets" 2>/dev/null \
    && ok "секреты переданы" || dim "секретов нет, сервер спросит сам"

  head1 "Запускаю установку на сервере"
  say "  ${C_DIM}Дальше вывод идёт прямо с $target${C_RESET}"
  say ""
  ssh -t "$target" "cd '$remote_dir' && chmod +x setup.sh deploy/*.sh && ./setup.sh --server --yes"
  local rc=$?

  say ""
  if [ $rc -eq 0 ]; then
    head1 "Развёрнуто"
    say "  ${C_DIM}Логи:  ssh $target 'cd samobranka && docker compose logs -f web'${C_RESET}"
    say "  ${C_DIM}Откат: ssh $target 'cd samobranka && ./deploy/rollback.sh'${C_RESET}"
  else
    bad "установка на сервере закончилась ошибкой (код $rc)"
    say "  ${C_DIM}Посмотреть: ssh $target 'cd samobranka && docker compose logs --tail 50'${C_RESET}"
    exit $rc
  fi
}

run_update() {
  local target_file="$PROJECT_DIR/deploy/.target"
  if [ -f "$target_file" ]; then
    ASSUME_YES=1 run_remote
  else
    ASSUME_YES=1 run_server
  fi
}

# ============================================================================

case "$MODE" in
  local)  run_local ;;
  server) run_server ;;
  remote) run_remote ;;
  update) run_update ;;
esac
