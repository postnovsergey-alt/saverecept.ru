#!/usr/bin/env bash
# Собирает .env: часть значений забирает из .env соседних проектов,
# часть генерирует, остальное спрашивает. Существующий .env не перезаписывает
# без спроса и всегда делает копию.
#
#   ./deploy/adopt_env.sh          интерактивно
#   ./deploy/adopt_env.sh --yes    без вопросов, всё что не нашлось — пустым

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
[ "${1:-}" = "--yes" ] && ASSUME_YES=1

ENV_FILE="$PROJECT_DIR/.env"
TEMPLATE="$PROJECT_DIR/.env.example"

say "${C_BOLD}Сборка .env${C_RESET}"

# --------------------------------------------------------- ищем соседей
head1 "Ищу креды у соседних проектов"

NEIGHBOR_ENVS=()
for root in "$HOME" "/opt" "/srv" "/var/www" "$(dirname "$PROJECT_DIR")"; do
  [ -d "$root" ] || continue
  while IFS= read -r f; do
    [ "$(dirname "$f")" = "$PROJECT_DIR" ] && continue
    NEIGHBOR_ENVS+=("$f")
  done < <(find "$root" -maxdepth 3 -name '.env' -type f 2>/dev/null)
done
# убираем дубли
mapfile -t NEIGHBOR_ENVS < <(printf '%s\n' "${NEIGHBOR_ENVS[@]:-}" | awk 'NF' | sort -u)

if [ ${#NEIGHBOR_ENVS[@]} -eq 0 ]; then
  warn "соседних .env не нашёл"
else
  for f in "${NEIGHBOR_ENVS[@]}"; do dim "нашёл: $f"; done
fi

# Достаёт значение ключа из соседских .env. Берёт первое непустое.
from_neighbors() {
  local key="$1" f val
  for f in "${NEIGHBOR_ENVS[@]:-}"; do
    [ -f "$f" ] || continue
    val=$(grep -m1 -E "^[[:space:]]*${key}[[:space:]]*=" "$f" 2>/dev/null \
          | sed -E 's/^[^=]*=[[:space:]]*//; s/^["'"'"']//; s/["'"'"']$//' | tr -d '\r')
    if [ -n "$val" ]; then printf '%s' "$val"; return 0; fi
  done
  return 1
}

# Пробует несколько имён — у разных проектов ключи называются по-разному
from_neighbors_any() {
  local key val
  for key in "$@"; do
    if val=$(from_neighbors "$key"); then
      printf '%s' "$val"
      return 0
    fi
  done
  return 1
}

# --------------------------------------------------------- собираем значения
head1 "Собираю значения"

LLM_KEY=""
if LLM_KEY=$(from_neighbors_any GEMINI_API_KEY GOOGLE_API_KEY GOOGLE_GENAI_API_KEY LLM_API_KEY); then
  ok "ключ LLM взят из .env соседнего проекта"
else
  warn "ключа LLM у соседей нет — получите на https://aistudio.google.com/apikey"
fi

FALLBACK_KEY=""
if FALLBACK_KEY=$(from_neighbors_any OPENROUTER_API_KEY OPENROUTER_KEY); then
  ok "нашёлся ключ OpenRouter — поставлю запасным провайдером"
fi

TG_TOKEN=""
if TG_TOKEN=$(from_neighbors_any SAMOBRANKA_BOT_TOKEN); then
  ok "токен бота Самобранки найден"
else
  dim "токен бота не найден — заведите нового бота у @BotFather (чужой брать нельзя)"
fi

TZ_VALUE=$(from_neighbors TZ 2>/dev/null || echo "Europe/Moscow")

SECRET=$(openssl rand -hex 32 2>/dev/null || head -c32 /dev/urandom | od -An -tx1 | tr -d ' \n')
ok "SECRET_KEY сгенерирован"

PORT=$(pick_free_port "${WEB_PORT:-8080}") || { bad "не нашёл свободный порт"; exit 1; }
ok "порт для сайта: $PORT (проверено, что не занят)"

DOMAIN=""
if [ "${ASSUME_YES:-0}" != "1" ]; then
  read -r -p "  Домен или адрес сайта (Enter — http://ЛОКАЛЬНЫЙ:$PORT): " DOMAIN
fi
if [ -z "$DOMAIN" ]; then
  HOST_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
  PUBLIC_URL="http://${HOST_IP:-localhost}:$PORT"
else
  [[ "$DOMAIN" =~ ^https?:// ]] || DOMAIN="https://$DOMAIN"
  PUBLIC_URL="${DOMAIN%/}"
fi
ok "публичный адрес: $PUBLIC_URL"

# --------------------------------------------------------- пишем файл
head1 "Записываю .env"

if [ -f "$ENV_FILE" ]; then
  BACKUP="$ENV_FILE.bak.$(date +%Y%m%d-%H%M%S)"
  cp "$ENV_FILE" "$BACKUP"
  ok "старый .env сохранён в $(basename "$BACKUP")"
  if ! confirm "  Перезаписать .env?"; then
    say "  Отменено, ничего не тронуто."
    exit 0
  fi
fi

cat > "$ENV_FILE" <<EOF
# Собрано ./deploy/adopt_env.sh $(date '+%d.%m.%Y %H:%M')

DATABASE_URL=sqlite:///./data/samobranka.db
MEDIA_DIR=./data/media
TZ=$TZ_VALUE

SECRET_KEY=$SECRET

WEB_PORT=$PORT
PUBLIC_BASE_URL=$PUBLIC_URL

# Gemini через OpenAI-совместимый эндпоинт.
# Список доступных моделей: python -m tools.check_llm --list
LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai
LLM_API_KEY=$LLM_KEY
LLM_MODEL=gemini-flash-latest

LLM_FALLBACK_BASE_URL=$([ -n "$FALLBACK_KEY" ] && echo "https://openrouter.ai/api/v1")
LLM_FALLBACK_API_KEY=$FALLBACK_KEY
LLM_FALLBACK_MODEL=$([ -n "$FALLBACK_KEY" ] && echo "google/gemini-2.0-flash-exp:free")

TELEGRAM_BOT_TOKEN=$TG_TOKEN

IMAGE_MAX_WIDTH=1280
IMAGE_THUMB_WIDTH=480
IMAGE_QUALITY=78
IMAGE_MAX_COUNT=4
EOF

chmod 600 "$ENV_FILE"
ok ".env записан, права 600"

head1 "Что осталось заполнить руками"
[ -z "$LLM_KEY" ]  && warn "LLM_API_KEY — без него страницы без разметки разберутся хуже"
[ -z "$TG_TOKEN" ] && warn "TELEGRAM_BOT_TOKEN — без него бот не поднимется, сайт работает"
say ""
say "  Аккаунты пользователи создают сами на /register."
say "  Дальше: ./deploy/deploy.sh"
