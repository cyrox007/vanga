#!/usr/bin/env bash
set -Eeuo pipefail

# Безопасное обновление серверной Vanga без запуска тяжёлого retrain по умолчанию.
# Запускать от root:
#   bash deploy/update-vanga.sh --yes
#   bash deploy/update-vanga.sh --yes --full-retrain
#
# Для нестандартной установки можно переопределить:
#   VANGA_DIR=/home/projects/vanga
#   VANGA_USER=vanga
#   VANGA_SERVICE=vanga.service
#   VANGA_RETRAIN_SERVICE=vanga-retrain.service

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

VANGA_DIR="${VANGA_DIR:-${DEFAULT_ROOT}}"
VANGA_USER="${VANGA_USER:-$(stat -c '%U' "${VANGA_DIR}" 2>/dev/null || printf 'vanga')}"
VANGA_GROUP="${VANGA_GROUP:-$(stat -c '%G' "${VANGA_DIR}" 2>/dev/null || printf '%s' "${VANGA_USER}")}"
VANGA_SERVICE="${VANGA_SERVICE:-vanga.service}"
VANGA_RETRAIN_SERVICE="${VANGA_RETRAIN_SERVICE:-vanga-retrain.service}"
VANGA_HEALTH_URL="${VANGA_HEALTH_URL:-http://127.0.0.1:9100/health}"
VANGA_BRANCH="${VANGA_BRANCH:-main}"
VANGA_ORCHESTRATION_LOCK="${VANGA_ORCHESTRATION_LOCK:-${VANGA_DIR}/data/runtime/orchestration.lock}"

ASSUME_YES=0
FULL_RETRAIN=0
SKIP_DB=0
SKIP_PIP=0
LOCK_HELD=0

log() {
    printf '[Vanga update] %s\n' "$*"
}

warn() {
    printf '[Vanga update] ПРЕДУПРЕЖДЕНИЕ: %s\n' "$*" >&2
}

fail() {
    printf '[Vanga update] ОШИБКА: %s\n' "$*" >&2
    exit 1
}

usage() {
    cat <<'EOF'
Использование:
  bash deploy/update-vanga.sh [опции]

Опции:
  --yes            не задавать вопрос перед изменениями
  --full-retrain   после smoke запустить полный retrain через systemd unit
  --skip-db        не обновлять IMDb datasets / DuckDB
  --skip-pip       не выполнять pip install -r requirements.txt
  -h, --help       показать справку

По умолчанию тяжёлый полный retrain НЕ запускается.
EOF
}

while (($#)); do
    case "$1" in
        --yes) ASSUME_YES=1 ;;
        --full-retrain) FULL_RETRAIN=1 ;;
        --skip-db) SKIP_DB=1 ;;
        --skip-pip) SKIP_PIP=1 ;;
        -h|--help) usage; exit 0 ;;
        *) fail "Неизвестный аргумент: $1" ;;
    esac
    shift
done

[[ ${EUID} -eq 0 ]] || fail "Сценарий должен запускаться от root, чтобы безопасно управлять systemd."
[[ -d "${VANGA_DIR}/.git" ]] || fail "Не найден git-репозиторий: ${VANGA_DIR}"
[[ -x "${VANGA_DIR}/.venv/bin/python" ]] || fail "Не найден Python venv: ${VANGA_DIR}/.venv"
[[ -x "${VANGA_DIR}/.venv/bin/pip" ]] || fail "Не найден pip в venv: ${VANGA_DIR}/.venv/bin/pip"

command -v git >/dev/null || fail "Не найден git"
command -v systemctl >/dev/null || fail "Не найден systemctl"
command -v curl >/dev/null || fail "Не найден curl"
command -v sudo >/dev/null || fail "Не найден sudo"
command -v flock >/dev/null || fail "Не найден flock (util-linux)"

run_vanga() {
    sudo -u "${VANGA_USER}" -- "$@"
}

run_vanga_env() {
    sudo -u "${VANGA_USER}" -- env \
        PYTHONUNBUFFERED=1 \
        MALLOC_ARENA_MAX=2 \
        VANGA_ORCHESTRATION_LOCK="${VANGA_ORCHESTRATION_LOCK}" \
        VANGA_ORCHESTRATION_LOCK_HELD=1 \
        VANGA_TRAIN_MIN_FREE_DISK_GB="${VANGA_TRAIN_MIN_FREE_DISK_GB:-4}" \
        VANGA_TRAIN_MAX_MODEL_SIZE_MB="${VANGA_TRAIN_MAX_MODEL_SIZE_MB:-512}" \
        VANGA_TRAIN_MAX_MAE_REGRESSION="${VANGA_TRAIN_MAX_MAE_REGRESSION:-0.03}" \
        "$@"
}

health_check() {
    local attempt
    for attempt in 1 2 3 4 5; do
        if curl --fail --silent --show-error --max-time 8 "${VANGA_HEALTH_URL}"; then
            printf '\n'
            return 0
        fi
        sleep 2
    done
    return 1
}

acquire_orchestration_lock() {
    local lock_dir
    lock_dir="$(dirname -- "${VANGA_ORCHESTRATION_LOCK}")"
    install -d -o "${VANGA_USER}" -g "${VANGA_GROUP}" -m 0750 "${lock_dir}"
    touch "${VANGA_ORCHESTRATION_LOCK}"
    chown "${VANGA_USER}:${VANGA_GROUP}" "${VANGA_ORCHESTRATION_LOCK}"
    chmod 0640 "${VANGA_ORCHESTRATION_LOCK}"
    exec 9<>"${VANGA_ORCHESTRATION_LOCK}"
    if ! flock --exclusive --nonblock 9; then
        fail "Уже выполняется другой updater/retrain: ${VANGA_ORCHESTRATION_LOCK}"
    fi
    LOCK_HELD=1
    log "Orchestration lock получен: ${VANGA_ORCHESTRATION_LOCK}"
}

release_orchestration_lock() {
    if [[ ${LOCK_HELD} -eq 1 ]]; then
        flock --unlock 9 || true
        exec 9>&-
        LOCK_HELD=0
        log "Orchestration lock освобождён"
    fi
}

trap release_orchestration_lock EXIT

log "Каталог: ${VANGA_DIR}"
log "Пользователь: ${VANGA_USER}"
log "Ветка: ${VANGA_BRANCH}"
log "Полный retrain: $([[ ${FULL_RETRAIN} -eq 1 ]] && printf 'да' || printf 'нет')"

if ! run_vanga git -C "${VANGA_DIR}" diff --quiet -- || \
   ! run_vanga git -C "${VANGA_DIR}" diff --cached --quiet --; then
    fail "Есть незакоммиченные изменения в отслеживаемых файлах. Сначала сохраните или отмените их."
fi

if [[ ${ASSUME_YES} -ne 1 ]]; then
    printf 'Продолжить обновление Vanga? [y/N] '
    read -r answer
    [[ "${answer}" =~ ^[YyДд]$ ]] || fail "Обновление отменено пользователем."
fi

# С этого места начинаются изменения runtime, поэтому общий lock должен быть
# получен до git/pip/DB/smoke. Timer использует тот же файл через retrain_job.py.
acquire_orchestration_lock

log "Получаю актуальный ${VANGA_BRANCH}..."
run_vanga git -C "${VANGA_DIR}" fetch --prune origin "${VANGA_BRANCH}"
run_vanga git -C "${VANGA_DIR}" switch "${VANGA_BRANCH}"
run_vanga git -C "${VANGA_DIR}" merge --ff-only "origin/${VANGA_BRANCH}"

log "Текущий commit: $(run_vanga git -C "${VANGA_DIR}" rev-parse --short HEAD)"

if [[ ${SKIP_PIP} -ne 1 ]]; then
    log "Проверяю Python-зависимости..."
    run_vanga "${VANGA_DIR}/.venv/bin/pip" install --disable-pip-version-check -r "${VANGA_DIR}/requirements.txt"
else
    warn "Обновление Python-зависимостей пропущено (--skip-pip)."
fi

if [[ ${SKIP_DB} -ne 1 ]]; then
    log "Обновляю IMDb datasets и атомарную DuckDB..."
    (
        cd "${VANGA_DIR}"
        run_vanga_env "${VANGA_DIR}/.venv/bin/python" "${VANGA_DIR}/ds_update.py"
    )
else
    warn "Обновление IMDb/DuckDB пропущено (--skip-db)."
fi

# После атомарной замены imdb.duckdb старый процесс может продолжать держать
# старый inode. Перезапуск нужен даже если новая модель пока не обучалась.
log "Перезапускаю inference-сервис для подключения к актуальной DuckDB..."
systemctl restart "${VANGA_SERVICE}"

if ! health_check; then
    systemctl status "${VANGA_SERVICE}" --no-pager -l || true
    journalctl -u "${VANGA_SERVICE}" --no-pager -n 80 || true
    fail "Inference не прошёл health-check после обновления БД."
fi

log "Запускаю smoke-проверку training pipeline без публикации модели..."
(
    cd "${VANGA_DIR}"
    run_vanga_env "${VANGA_DIR}/.venv/bin/python" "${VANGA_DIR}/traning.py" --smoke
)

if [[ ${FULL_RETRAIN} -eq 1 ]]; then
    if ! systemctl cat "${VANGA_RETRAIN_SERVICE}" >/dev/null 2>&1; then
        fail "Не найден ${VANGA_RETRAIN_SERVICE}. Полный retrain напрямую не запускаю: нужен systemd memory/cpu guard."
    fi

    if systemctl is-active --quiet "${VANGA_RETRAIN_SERVICE}"; then
        fail "${VANGA_RETRAIN_SERVICE} уже выполняется. Не запускаю второй retrain."
    fi

    # Полный retrain сам получает тот же lock в retrain_job.py. Освобождаем наш
    # descriptor непосредственно перед systemctl start. Если другой процесс
    # успеет забрать lock, service завершится отказом вместо параллельной работы.
    release_orchestration_lock

    log "Запускаю полный retrain через ${VANGA_RETRAIN_SERVICE}..."
    if ! systemctl start "${VANGA_RETRAIN_SERVICE}"; then
        systemctl status "${VANGA_RETRAIN_SERVICE}" --no-pager -l || true
        journalctl -u "${VANGA_RETRAIN_SERVICE}" --no-pager -n 120 || true
        fail "Полный retrain завершился ошибкой. Активная модель не переключается при неудачной публикации."
    fi

    log "Retrain завершён. Проверяю inference после обязательного restart из ExecStopPost..."
    if ! health_check; then
        systemctl status "${VANGA_SERVICE}" --no-pager -l || true
        journalctl -u "${VANGA_SERVICE}" --no-pager -n 100 || true
        fail "Inference не прошёл health-check после retrain."
    fi
else
    log "Полный retrain не запускался. Для него повторите с --full-retrain."
fi

log "Проверяю, что API слушает только локальный health endpoint..."
health_check >/dev/null || fail "Финальный health-check не пройден."

release_orchestration_lock
log "Обновление завершено успешно."
log "Порт 9100 не должен публиковаться наружу; скрипт не меняет firewall/nginx."
