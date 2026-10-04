# P8. Pre-release Audience Signals

P8 хранит только **агрегатные публичные/лицензированные сигналы**, которые реально были доступны до релиза фильма.

## Что допускается

Foundation поддерживает числовые типы:

- `trailer_views`;
- `trailer_likes`;
- `trailer_comments`;
- `search_interest_index`;
- `mention_volume`;
- `sentiment_mean`;
- `sentiment_polarization`;
- `wishlist_count`.

Это не означает автоматического включения в CatBoost. Любой feature сначала должен пройти P6 registry + temporal audit + ablation.

## Что не хранится

`audience_signals.duckdb` не содержит:

- текстов постов/комментариев;
- user ids / usernames;
- демографии отдельных пользователей;
- персональных профилей;
- чувствительных характеристик аудитории.

Для sentiment хранится только уже рассчитанный агрегат и versioned method.

## Temporal contract

У observation есть:

- `observed_at` — когда измерен сигнал;
- `known_at` — когда он стал доступен Vanga;
- `planned_release_at` — дата релиза, относительно которой наблюдение считалось pre-release;
- `method` + `method_version`;
- `source_id` и usage basis.

Registry принимает observation только если **и `observed_at`, и `known_at` строго раньше `planned_release_at`**.

`features_as_of()` также требует текущий `release_at` и fail-closed, если `cutoff >= release_at`. Поэтому перенос релиза назад не позволяет использовать уже пострелизный сигнал как pre-release.

## Exact protocol

Разные методы не смешиваются автоматически. Feature spec обязан указать:

```json
{
  "signal_type": "trailer_views",
  "method": "youtube_public_stats",
  "method_version": "1",
  "unit": "count",
  "feature_name": "audience_trailer_views"
}
```

Для feature возвращаются:

- значение;
- `<feature>_known`;
- `<feature>_age_days`;
- `<feature>_coverage`;
- `<feature>_sample_size`.

Missing остаётся `known=0`; ноль в value является только числовым filler и не выдаётся за реальное наблюдение.

## Provenance / usage basis

Источник обязан иметь один из contracts:

- `public_aggregate`;
- `licensed`;
- `first_party`;
- `manual_reference`.

Это позволяет в будущем подключать разные collectors, не смешивая происхождение данных.

## CLI

Импорт воспроизводимого bundle:

```bash
python scripts/audience_signals.py import-bundle bundle.json
```

As-of snapshot:

```bash
python scripts/audience_signals.py snapshot film-1 \
  --cutoff 2026-06-01T00:00:00Z \
  --release-at 2026-08-01T00:00:00Z
```

Exact protocol features:

```bash
python scripts/audience_signals.py features film-1 protocols.json \
  --cutoff 2026-06-01T00:00:00Z \
  --release-at 2026-08-01T00:00:00Z
```

## Граница этапа

P8 foundation пока не включает сетевые collectors и не делает предположение, что высокий hype обязательно повышает качество фильма. Сначала нужен coverage, затем отдельный P6 temporal ablation каждого протокола.
