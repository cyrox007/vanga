# Серверное обновление Vanga

Для рабочего VPS используйте `deploy/update-vanga.sh`. Скрипт рассчитан на уже установленную Vanga с `.venv` и systemd-сервисом inference.

## Обычное обновление без полного retrain

```bash
cd /home/projects/vanga
VANGA_DIR=/home/projects/vanga \
VANGA_USER=vanga \
bash deploy/update-vanga.sh --yes
```

Сценарий:

1. проверяет, что отслеживаемые git-файлы не изменены локально;
2. делает `fetch` и только fast-forward обновление `main`;
3. обновляет Python-зависимости;
4. обновляет IMDb datasets и атомарно пересобирает DuckDB при необходимости;
5. перезапускает `vanga.service`, чтобы процесс открыл актуальный файл DuckDB;
6. проверяет `http://127.0.0.1:9100/health`;
7. запускает `traning.py --smoke` без публикации модели;
8. оставляет текущую опубликованную модель активной.

Полный retrain намеренно не запускается автоматически.

## Обновление с полным retrain

```bash
cd /home/projects/vanga
VANGA_DIR=/home/projects/vanga \
VANGA_USER=vanga \
bash deploy/update-vanga.sh --yes --full-retrain
```

Полное обучение запускается только через `vanga-retrain.service`. Это принципиально: unit должен содержать ограничения RAM/CPU/swap. Если unit отсутствует, скрипт завершится с ошибкой и не станет запускать тяжёлое обучение напрямую.

Candidate-модель публикуется только после проверок размера, metadata и quality gate. При неудачном retrain `models/current.json` остаётся на предыдущем поколении.

## Полезные переменные

```bash
export VANGA_DIR=/home/projects/vanga
export VANGA_USER=vanga
export VANGA_SERVICE=vanga.service
export VANGA_RETRAIN_SERVICE=vanga-retrain.service
export VANGA_HEALTH_URL=http://127.0.0.1:9100/health
export VANGA_TRAIN_MIN_FREE_DISK_GB=4
export VANGA_TRAIN_MAX_MODEL_SIZE_MB=512
export VANGA_TRAIN_MAX_MAE_REGRESSION=0.03
```

## Важно для текущего VPS

Порт `9100` должен оставаться привязан к `127.0.0.1`. Скрипт не меняет Nginx и firewall и не публикует inference API наружу.

Если установка находится в `/home/projects/vanga`, systemd unit должен использовать этот путь и `ProtectHome=false`. Репозиторный шаблон для `/opt/vanga` нельзя копировать вслепую поверх уже работающего unit.
