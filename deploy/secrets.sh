#!/usr/bin/env bash
# Единственное место, где вообще требуется человек.
#
# Здесь спрашиваются только те значения, которые невозможно получить
# программно: их выдают внешние сервисы после входа под вашей учёткой.
# Всё остальное скрипты добывают сами.
#
# Ответы сохраняются в deploy/.secrets (права 600, в git не попадает),
# поэтому спрашивается это один раз в жизни проекта.

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

SECRETS_FILE="$STATE_DIR/../.secrets"

load_secrets() {
  [ -f "$SECRETS_FILE" ] && { set -a; source "$SECRETS_FILE"; set +a; }
  return 0
}

save_secrets() {
  cat > "$SECRETS_FILE" <<EOF
# Секреты Самобранки. В git не попадает (см. .gitignore).
# Чтобы поменять — просто отредактируйте этот файл или удалите и запустите заново.
SECRET_GEMINI_KEY='${SECRET_GEMINI_KEY:-}'
SECRET_TG_TOKEN='${SECRET_TG_TOKEN:-}'
SECRET_DOMAIN='${SECRET_DOMAIN:-}'
EOF
  chmod 600 "$SECRETS_FILE"
}

# Спрашивает значение, если его ещё нет. Пустой ответ разрешён — Самобранка
# работает и без ключей, просто с урезанными возможностями.
ask_once() {
  local var="$1" prompt="$2" hint="$3"
  local current="${!var:-}"
  [ -n "$current" ] && return 0
  [ "${ASSUME_YES:-0}" = "1" ] && return 0

  say ""
  say "  ${C_BOLD}$prompt${C_RESET}"
  printf '  %s%s%s\n' "$C_DIM" "$hint" "$C_RESET"
  read -r -p "  > " value
  printf -v "$var" '%s' "$value"
}

collect_secrets() {
  load_secrets

  # уже спрашивали — молчим
  [ -f "$SECRETS_FILE" ] && return 0

  # автоматический режим: ничего не спрашиваем, работаем с тем, что есть
  if [ "${ASSUME_YES:-0}" = "1" ]; then
    save_secrets
    return 0
  fi

  head1 "Что нужно от вас"
  say "  Три значения, которые нельзя получить автоматически: их выдают Google"
  say "  и Telegram после входа под вашей учётной записью. Спрошу один раз."
  say "  ${C_DIM}Любое можно пропустить, нажав Enter — потом допишете в .env.${C_RESET}"

  ask_once SECRET_GEMINI_KEY \
    "Ключ Gemini" \
    "Откройте https://aistudio.google.com/apikey → Create API key → создайте НОВЫЙ проект.
  Биллинг на нём не включайте, иначе бесплатный тариф пропадёт. Enter — пропустить."

  ask_once SECRET_TG_TOKEN \
    "Токен Telegram-бота" \
    "Напишите @BotFather команду /newbot, придумайте имя, скопируйте токен.
  Enter — пропустить, сайт будет работать и без бота. Бот привязывается к
  аккаунту через одноразовый код из профиля на сайте."

  ask_once SECRET_DOMAIN \
    "Домен сайта" \
    "Например samobranka.example.ru. Enter — обойдёмся адресом вида http://IP:порт."

  save_secrets
  ok "секреты сохранены в deploy/.secrets, больше спрашивать не буду"
}

# при запуске напрямую — просто собрать секреты
if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
  [ "${1:-}" = "--reset" ] && rm -f "$SECRETS_FILE" && ok "старые ответы удалены"
  collect_secrets
fi
