#!/usr/bin/env bash
set -Eeuo pipefail

# Транзакционное обновление серверной Vanga без тяжёлого retrain по умолчанию.
# Новые зависимости и IMDb DB сначала собираются в staging, smoke выполняется
# под systemd resource limits, и только затем переключается runtime.
# Запускать от root:
#   bash deploy/update-vanga.sh --yes
#   bash deploy/update-vanga.sh --yes --full-retrain

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
ACTIVE_DB="${VANGA_IMDB_DB:-${VANGA_DIR}/imdb.duckdb}"
ACTIVE_VENV="${VANGA_DIR}/.venv"
ACTIVE_MANIFEST="${VANGA_DIR}/data/imdb/freshness-manifest.json"

ASSUME_YES=0
FULL_RETRAIN=0
SKIP_DB=0
SKIP_PIP=0
LOCK_HELD=0
CODE_UPDATED=0
RUNTIME_SWITCHED=0
UPDATE_SUCCEEDED=0
SERVICE_WAS_ACTIVE=0
DB_EXISTED=0
POINTER_EXISTED=0

PREVIOUS_COMMIT=""
STAGE_ROOT=""
VENV_CANDIDATE=""
VENV_ROLLBACK=""
DB_CANDIDATE=""
DB_ROLLBACK=""
CANDIDATE_MANIFEST=""
POINTER_PATH="${VANGA_DIR}/models/current.json"
POINTER_ROLLBACK=""

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
  --full-retrain   после переключения runtime выполнить полный retrain под systemd limits
  --skip-db        не обновлять IMDb datasets / DuckDB
  --skip-pip       не собирать новый venv из requirements.txt
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
[[ -x "${ACTIVE_VENV}/bin/python" ]] || fail "Не найден Python venv: ${ACTIVE_VENV}"
[[ -x "${ACTIVE_VENV}/bin/pip" ]] || fail "Не найден pip в venv: ${ACTIVE_VENV}/bin/pip"

command -v git >/dev/null || fail "Не найден git"
command -v systemctl >/dev/null || fail "Не найден systemctl"
command -v systemd-run >/dev/null || fail "Не найден systemd-run"
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

prepare_stage() {
    STAGE_ROOT="${VANGA_DIR}/data/runtime/update-${BASHPID}"
    VENV_CANDIDATE="${VANGA_DIR}/.venv.candidate-${BASHPID}"
    VENV_ROLLBACK="${VANGA_DIR}/.venv.rollback-${BASHPID}"
    DB_CANDIDATE="${STAGE_ROOT}/imdb.duckdb"
    DB_ROLLBACK="${STAGE_ROOT}/imdb.rollback.duckdb"
    CANDIDATE_MANIFEST="${STAGE_ROOT}/freshness-manifest.json"
    POINTER_ROLLBACK="${STAGE_ROOT}/current.json.rollback"

    rm -rf -- "${STAGE_ROOT}" "${VENV_CANDIDATE}" "${VENV_ROLLBACK}"
    install -d -o "${VANGA_USER}" -g "${VANGA_GROUP}" -m 0750 "${STAGE_ROOT}"
}

cleanup_stage() {
    if [[ -n ${VENV_CANDIDATE} && -d ${VENV_CANDIDATE} ]]; then
        rm -rf -- "${VENV_CANDIDATE}" || true
    fi
    if [[ ${UPDATE_SUCCEEDED} -eq 1 && -n ${VENV_ROLLBACK} && -d ${VENV_ROLLBACK} ]]; then
        rm -rf -- "${VENV_ROLLBACK}" || true
    fi
    if [[ -n ${STAGE_ROOT} && -d ${STAGE_ROOT} ]]; then
        rm -rf -- "${STAGE_ROOT}" || true
    fi
}

run_limited_training() {
    local unit_name="$1"
    local python_bin="$2"
    local imdb_db="$3"
    shift 3

    systemd-run \
        --quiet \
        --wait \
        --collect \
        --pipe \
        --unit="${unit_name}" \
        --property=Type=oneshot \
        --property="User=${VANGA_USER}" \
        --property="Group=${VANGA_GROUP}" \
        --property="WorkingDirectory=${VANGA_DIR}" \
        --property=Nice=15 \
        --property=CPUQuota=50% \
        --property=MemoryHigh=900M \
        --property=MemoryMax=1100M \
        --property=MemorySwapMax=2G \
        --setenv=PYTHONUNBUFFERED=1 \
        --setenv=MALLOC_ARENA_MAX=2 \
        --setenv="VANGA_IMDB_DB=${imdb_db}" \
        --setenv="VANGA_ORCHESTRATION_LOCK=${VANGA_ORCHESTRATION_LOCK}" \
        --setenv=VANGA_ORCHESTRATION_LOCK_HELD=1 \
        --setenv="VANGA_TRAIN_MIN_FREE_DISK_GB=${VANGA_TRAIN_MIN_FREE_DISK_GB:-4}" \
        --setenv="VANGA_TRAIN_MAX_MODEL_SIZE_MB=${VANGA_TRAIN_MAX_MODEL_SIZE_MB:-512}" \
        --setenv="VANGA_TRAIN_MAX_MAE_REGRESSION=${VANGA_TRAIN_MAX_MAE_REGRESSION:-0.03}" \
        "${python_bin}" "${VANGA_DIR}/traning.py" "$@"
}

backup_runtime() {
    if [[ -f ${POINTER_PATH} ]]; then
        cp -a -- "${POINTER_PATH}" "${POINTER_ROLLBACK}"
        POINTER_EXISTED=1
    fi

    if [[ ${SKIP_DB} -ne 1 && -f ${ACTIVE_DB} ]]; then
        # ds_update и публикация используют rename; hardlink сохраняет прежний
        # inode без второй полной копии многогигабайтной DuckDB.
        ln -- "${ACTIVE_DB}" "${DB_ROLLBACK}"
        DB_EXISTED=1
    fi
}

rollback_update() {
    warn "Ошибка обновления — возвращаю предыдущий runtime"

    if [[ ${RUNTIME_SWITCHED} -eq 1 ]]; then
        systemctl stop "${VANGA_SERVICE}" >/dev/null 2>&1 || true

        if [[ -d ${VENV_ROLLBACK} ]]; then
            rm -rf -- "${ACTIVE_VENV}" || true
            mv -- "${VENV_ROLLBACK}" "${ACTIVE_VENV}" || warn "Не удалось восстановить прежний venv"
        fi

        if [[ ${SKIP_DB} -ne 1 ]]; then
            if [[ ${DB_EXISTED} -eq 1 && -f ${DB_ROLLBACK} ]]; then
                rm -f -- "${ACTIVE_DB}"
                mv -- "${DB_ROLLBACK}" "${ACTIVE_DB}" || warn "Не удалось восстановить прежнюю IMDb DB"
            elif [[ ${DB_EXISTED} -eq 0 ]]; then
                rm -f -- "${ACTIVE_DB}"
            fi
        fi

        if [[ ${POINTER_EXISTED} -eq 1 && -f ${POINTER_ROLLBACK} ]]; then
            cp -a -- "${POINTER_ROLLBACK}" "${POINTER_PATH}" || warn "Не удалось восстановить model pointer"
        elif [[ ${POINTER_EXISTED} -eq 0 ]]; then
            rm -f -- "${POINTER_PATH}"
        fi
    fi

    if [[ ${CODE_UPDATED} -eq 1 && -n ${PREVIOUS_COMMIT} ]]; then
        run_vanga git -C "${VANGA_DIR}" reset --hard "${PREVIOUS_COMMIT}" >/dev/null || warn "Не удалось вернуть предыдущий commit"
    fi

    if [[ ${RUNTIME_SWITCHED} -eq 1 && ${SERVICE_WAS_ACTIVE} -eq 1 ]]; then
        if ! systemctl restart "${VANGA_SERVICE}"; then
            warn "Не удалось перезапустить прежний inference после rollback"
        elif ! health_check >/dev/null 2>&1; then
            warn "Прежний inference после rollback не прошёл health-check"
        else
            log "Предыдущий runtime восстановлен и отвечает на health-check"
        fi
    fi
}

on_exit() {
    local status=$?
    trap - EXIT
    if [[ ${status} -ne 0 && ${UPDATE_SUCCEEDED} -ne 1 ]]; then
        rollback_update
    fi
    cleanup_stage
    release_orchestration_lock
    exit "${status}"
}

trap on_exit EXIT

log "Каталог: ${VANGA_DIR}"
log "Пользователь: ${VANGA_USER}"
log "Ветка: ${VANGA_BRANCH}"
log "Полный retrain: $([[ ${FULL_RETRAIN} -eq 1 ]] && printf 'да' || printf 'нет')"

if systemctl is-active --quiet "${VANGA_SERVICE}"; then
    SERVICE_WAS_ACTIVE=1
fi

if ! run_vanga git -C "${VANGA_DIR}" diff --quiet -- || \
   ! run_vanga git -C "${VANGA_DIR}" diff --cached --quiet --; then
    fail "Есть незакоммиченные изменения в отслеживаемых файлах. Сначала сохраните или отмените их."
fi

if [[ ${ASSUME_YES} -ne 1 ]]; then
    printf 'Продолжить обновление Vanga? [y/N] '
    read -r answer
    [[ "${answer}" =~ ^[YyДд]$ ]] || fail "Обновление отменено пользователем."
fi

acquire_orchestration_lock
prepare_stage
PREVIOUS_COMMIT="$(run_vanga git -C "${VANGA_DIR}" rev-parse HEAD)"

log "Получаю актуальный ${VANGA_BRANCH}..."
run_vanga git -C "${VANGA_DIR}" fetch --prune origin "${VANGA_BRANCH}"
run_vanga git -C "${VANGA_DIR}" switch "${VANGA_BRANCH}"
run_vanga git -C "${VANGA_DIR}" merge --ff-only "origin/${VANGA_BRANCH}"
CODE_UPDATED=1
log "Candidate commit: $(run_vanga git -C "${VANGA_DIR}" rev-parse --short HEAD)"

SMOKE_PYTHON="${ACTIVE_VENV}/bin/python"
if [[ ${SKIP_PIP} -ne 1 ]]; then
    log "Собираю candidate venv без изменения активного окружения..."
    run_vanga "${ACTIVE_VENV}/bin/python" -m venv --copies "${VENV_CANDIDATE}"
    run_vanga "${VENV_CANDIDATE}/bin/pip" install --disable-pip-version-check -r "${VANGA_DIR}/requirements.txt"
    SMOKE_PYTHON="${VENV_CANDIDATE}/bin/python"
else
    warn "Сборка candidate venv пропущена (--skip-pip); smoke использует активный venv."
fi

SMOKE_DB="${ACTIVE_DB}"
if [[ ${SKIP_DB} -ne 1 ]]; then
    log "Собираю candidate IMDb DuckDB без переключения активной БД..."
    (
        cd "${VANGA_DIR}"
        run_vanga_env \
            VANGA_IMDB_DB="${DB_CANDIDATE}" \
            VANGA_FRESHNESS_MANIFEST_PATH="${CANDIDATE_MANIFEST}" \
            VANGA_SKIP_RATING_HISTORY=1 \
            "${SMOKE_PYTHON}" "${VANGA_DIR}/ds_update.py"
    )
    [[ -s ${DB_CANDIDATE} ]] || fail "Candidate IMDb DB не создана"
    SMOKE_DB="${DB_CANDIDATE}"
else
    warn "Обновление IMDb/DuckDB пропущено (--skip-db)."
fi

log "Запускаю smoke candidate runtime под systemd CPU/RAM/swap limits..."
run_limited_training \
    "vanga-smoke-update-${BASHPID}" \
    "${SMOKE_PYTHON}" \
    "${SMOKE_DB}" \
    --smoke

log "Smoke пройден. Подготавливаю rollback предыдущего runtime..."
backup_runtime
RUNTIME_SWITCHED=1

if [[ ${SKIP_PIP} -ne 1 ]]; then
    mv -- "${ACTIVE_VENV}" "${VENV_ROLLBACK}"
    mv -- "${VENV_CANDIDATE}" "${ACTIVE_VENV}"
fi

if [[ ${SKIP_DB} -ne 1 ]]; then
    mv -f -- "${DB_CANDIDATE}" "${ACTIVE_DB}"

    # Manifest staging содержит candidate path. После публикации строим новый
    # manifest уже по фактическому production path.
    run_vanga "${ACTIVE_VENV}/bin/python" "${VANGA_DIR}/scripts/data_freshness.py" \
        report \
        --db "${ACTIVE_DB}" \
        --data-dir "${VANGA_DIR}/data/imdb" \
        --write-manifest "${ACTIVE_MANIFEST}" >/dev/null

    # P7 capture выполняется только после успешной публикации DB. Ошибка этого
    # накопительного слоя не откатывает основной runtime, но остаётся в логах.
    source_fingerprint="$(run_vanga "${ACTIVE_VENV}/bin/python" -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("logical_fingerprint_sha256") or "")' "${ACTIVE_MANIFEST}")"
    rating_args=("${ACTIVE_VENV}/bin/python" -m scripts.rating_history capture --imdb-db "${ACTIVE_DB}")
    if [[ -n ${source_fingerprint} ]]; then
        rating_args+=(--source-fingerprint "${source_fingerprint}")
    fi
    (
        cd "${VANGA_DIR}"
        if ! run_vanga "${rating_args[@]}"; then
            warn "P7 Rating History snapshot не записан; основной runtime оставлен опубликованным"
        fi
    )
fi

log "Перезапускаю inference только после зелёного candidate smoke..."
systemctl restart "${VANGA_SERVICE}"
if ! health_check; then
    systemctl status "${VANGA_SERVICE}" --no-pager -l || true
    journalctl -u "${VANGA_SERVICE}" --no-pager -n 100 || true
    fail "Новый runtime не прошёл health-check"
fi

if [[ ${FULL_RETRAIN} -eq 1 ]]; then
    log "Запускаю полный retrain под теми же systemd resource limits..."
    run_limited_training \
        "vanga-full-update-${BASHPID}" \
        "${ACTIVE_VENV}/bin/python" \
        "${ACTIVE_DB}"

    # Полный training может атомарно переключить model pointer. Перезапускаем
    # process, чтобы освободить предыдущий CatBoost до проверки нового generation.
    systemctl restart "${VANGA_SERVICE}"
    if ! health_check; then
        systemctl status "${VANGA_SERVICE}" --no-pager -l || true
        journalctl -u "${VANGA_SERVICE}" --no-pager -n 120 || true
        fail "Inference не прошёл health-check после full retrain"
    fi
else
    log "Полный retrain не запускался. Для него повторите с --full-retrain."
fi

health_check >/dev/null || fail "Финальный health-check не пройден"

UPDATE_SUCCEEDED=1
RUNTIME_SWITCHED=0
rm -rf -- "${VENV_ROLLBACK}" || true
rm -f -- "${DB_ROLLBACK}" || true
release_orchestration_lock
log "Обновление завершено успешно. Rollback-артефакты очищены."
log "Порт 9100 не должен публиковаться наружу; скрипт не меняет firewall/nginx."
