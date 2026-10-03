# P6 — Pre-release Proxy Hypothesis Registry

## Назначение

Retrospective Analyzer может обнаружить полезную закономерность уже после выхода фильма, но такой результат нельзя напрямую передать в pre-release Vanga.

P6 вводит обязательную цепочку:

```text
retrospective structural finding
        ↓ evidence/reference only
proxy hypothesis
        ↓ только заранее доступные candidate features
preregistered temporal ablation
        ↓
TemporalProxyAvailabilityAuditor
        ↓
GenericProxyAblationGate
        ↓
immutable result history
```

Registry живёт отдельно в `proxy_hypotheses.duckdb` и **не подключается к CatBoost автоматически**.

## Retrospective evidence

Допустимые виды evidence:

- `storydiff`;
- `story_transform`;
- `expert_corpus`;
- `adaptation_analysis`;
- `production_context_outcome`.

Evidence хранится как kind, `reference_id`, краткая observation summary и confidence. Полный сторонний текст, transcript и `expert_interpretation` здесь не хранятся и никогда не являются model feature.

## Pre-release proxy sources

Foundation разрешает:

- `imdb_history` → `history_before_target_year`;
- `source_context` → `known_at_lte_cutoff`, `published_at_lte_cutoff`, `planned_before_release`;
- `production_context` → `known_at_lte_cutoff`;
- `pre_release_public_signal` → `known_at_lte_cutoff`/`published_at_lte_cutoff`.

Proxy обязан иметь `available_before_release=true` и явный temporal contract.

## Forbidden feature guard

Запрещены retrospective namespaces:

- `retro_adapt_*`;
- `storydiff_*` / `story_diff_*`;
- `story_transform_*`;
- `expert_*`;
- `post_release_*`.

Также запрещены current/actual/future rating targets и expert/critic scores. Исторические рейтинговые признаки допустимы только через прошлые работы с `history_before_target_year`.

## Coverage и missingness

Отсутствие source/production данных не является нейтральным качеством. Proxy может иметь отдельный `coverage_feature`; materialization обязана явно различать available и missing rows.

## Готовность к ablation

Hypothesis получает `ablation_ready` только если есть:

1. retrospective evidence;
2. хотя бы один pre-release proxy;
3. preregistered ablation spec.

Ablation spec заранее фиксирует baseline schema, candidate label, stable temporal holdout, MAE rule, допустимую regression и обязательность dataset fingerprint.

Экспортированный plan получает SHA-256 fingerprint.

## Единственный quality gate

Quality comparison выполняет только:

```text
src/proxy_ablation.py::GenericProxyAblationGate
```

Перед ним обязателен:

```text
TemporalProxyAvailabilityAuditor
```

Именно этот путь проверяет:

- materialized facts относительно target cutoff;
- explicit missingness;
- plan fingerprint;
- audit fingerprint;
- один dataset fingerprint baseline/candidate;
- одинаковый temporal holdout;
- чистый feature delta;
- preregistered MAE rule.

Подробный протокол описан в `docs/P6_PROXY_ABLATION.md`.

**Второго независимого MAE gate нет.** Это принципиально: нельзя получить другой verdict, обойдя temporal audit.

## Immutable result history

`src/proxy_ablation_gate.py` после cleanup — это только persistence-слой над уже готовым отчётом `GenericProxyAblationGate`.

`ProxyAblationResultRegistry`:

- пересчитывает `gate_fingerprint_sha256` и отвергает изменённый отчёт;
- проверяет binding к текущим hypothesis/plan/audit/dataset fingerprints;
- сохраняет baseline/candidate MAE, delta, verdict и runner metadata;
- делает запись идемпотентной;
- не позволяет один gate report записать под двумя result IDs;
- **не пересчитывает MAE rule повторно**;
- **не переводит hypothesis автоматически в accepted/rejected**;
- не публикует модель.

Один успешный temporal holdout ещё не считается достаточным доказательством для production. Hypothesis остаётся `ablation_ready`, пока отдельная будущая policy не потребует нужный набор повторных окон/external-transfer проверок и явную финализацию.

Результаты хранятся в `proxy_ablation_run_results`.

## CLI

Registry гипотез:

```bash
python scripts/proxy_hypotheses.py init
python scripts/proxy_hypotheses.py validate worldbuilding-compression
python scripts/proxy_hypotheses.py mark-ready worldbuilding-compression
python scripts/proxy_hypotheses.py export-plan \
  worldbuilding-compression \
  --output temp/worldbuilding-compression-ablation.json
```

Temporal audit и единственный generic gate:

```bash
python scripts/proxy_ablation.py audit \
  temp/worldbuilding-compression-ablation.json \
  temp/materialization.json \
  --output temp/audit.json

python scripts/proxy_ablation.py gate \
  temp/worldbuilding-compression-ablation.json \
  temp/audit.json \
  temp/baseline-result.json \
  temp/candidate-result.json \
  --output temp/gate.json
```

Проверить result envelope перед записью:

```bash
python scripts/proxy_ablation_gate.py verify temp/result-envelope.json
```

Записать immutable history:

```bash
python scripts/proxy_ablation_gate.py record \
  temp/result-envelope.json \
  --output temp/recorded-result.json
```

История:

```bash
python scripts/proxy_ablation_gate.py history worldbuilding-compression
```

## Что P6 намеренно не делает

Он не:

- считает retrospective correlation причинностью;
- делает мнение эксперта model feature;
- автоматически публикует accepted candidate;
- позволяет менять plan после просмотра результата;
- принимает отсутствие данных за ноль;
- создаёт второй training pipeline.

Следующий P6-инкремент — **source-specific materializers/adapters** для уже существующих pre-release хранилищ: Source Context, Production Context и IMDb historical features. Они должны автоматически выдавать materialization protocol для обязательного `TemporalProxyAvailabilityAuditor`, не меняя audit/gate contract.
