# P5 — consensus и leave-one-expert-out экспертов

## Назначение

Этот слой дополняет `ExpertAgreementAnalyzer` из `src/expert_agreement_transfer.py`.

Pairwise agreement уже отвечает на вопрос, где два эксперта совпадают или расходятся. `ExpertConsensusAnalyzer` добавляет два отдельных исследования:

1. сколько экспертов поддерживают каждый `change_type` внутри одного `case_id + dimension`;
2. сохраняется ли разметка эксперта, если сравнивать её только с majority остальных профилей.

Это **не голосование за истину** и не способ получить «средний экспертный вкус».

## Scope

В анализ попадают только dimensions, реально размеченные экспертами в corpus.

Молчание эксперта не считается:

- отрицательной меткой;
- disagreement;
- голосом против.

По умолчанию действуют те же gold-правила, что и в agreement/transfer evaluator:

- `confidence >= 0.5`;
- обязательное supporting evidence.

## Consensus report

Для `case_id + dimension`, где есть минимум два эксперта, возвращаются:

- `expert_count`;
- `unanimous`;
- `change_type_votes`;
- `majority_threshold`;
- `majority_change_types`.

Majority считается строгим большинством `floor(N/2)+1`. Если большинства нет, массив остаётся пустым — система не придумывает consensus.

## Leave-one-expert-out

Для каждого профиля:

1. профиль исключается;
2. для каждого совместно размеченного scope считается majority оставшихся экспертов;
3. исходная разметка исключённого профиля сравнивается с peer-majority;
4. возвращаются exact-match rate и Jaccard.

Это не заменяет `HeldOutExpertTransferEvaluator`: transfer проверяет отдельный prediction run, построенный без held-out профиля. Consensus leave-one-out анализирует только уже существующую corpus-разметку.

## Защита от смешивания вкуса

Отчёт явно содержит:

- `consensus_is_ground_truth=false`;
- `preference_score=null`;
- `winner=null`.

В report не попадают тексты:

- `claim_summary`;
- `observation`;
- `structural_consequence`;
- `expert_interpretation`.

## CLI

```bash
python scripts/expert_consensus.py \
  --split blind \
  --min-gold-confidence 0.5 \
  --output temp/expert-consensus.json
```

## Использование в P5/P6

Практический порядок:

1. blind validation — может ли Analyzer найти структурные классы без expert labels;
2. pairwise agreement — одинаково ли независимые эксперты размечают эти классы;
3. held-out transfer — переносится ли методика на полностью исключённый expert profile;
4. consensus/leave-one-out — держится ли наблюдение после исключения одного профиля;
5. только после этого для устойчивых структурных закономерностей проектируется pre-release proxy P6.
