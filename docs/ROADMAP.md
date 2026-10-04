- [ ] Не использовать расу, этничность и другие чувствительные характеристики людей как признаки качества/рейтинга.

## P9. Future-release discovery

Официальные IMDb datasets могут плохо покрывать далёкие будущие проекты. Нужен отдельный discovery/enrichment слой.

- [ ] Каталог будущих релизов с source provenance и датой последнего обновления.
- [ ] Нормализовать фильм, режиссёров, сценаристов, актёров, source material, franchise, production label и release date. Команда/source/franchise/production company и temporal runtime/genres уже покрыты; безопасная release-date semantics ещё не завершена.
- [x] Не делать inference зависимым от сетевого API: найденные данные кешировать локально.
- [x] При конфликте источников хранить provenance/confidence, а не молча выбирать значение.

## P10. Публичный продукт на jsint-site

- [x] Основной prediction UX, autocomplete, AJAX, explanation, uncertainty.
- [x] Snapshots, share URLs, browser history, what-if compare.
- [x] «Vanga против реальности» и methodology.
- [x] Показ `data_coverage` и причин низкой обеспеченности данными.
- [ ] UI для нескольких режиссёров с autocomplete/chips и сохранением порядка.
- [ ] UI для расширенного актёрского состава: top-3 персональные признаки + Full Cast coverage/genre summary без визуальной перегрузки.
- [ ] Понятный grouped SHAP: сценарий/режиссура/режиссёрская команда/актёры/full cast/actor-pair familiarity/dual-role/team-wide collaboration/жанр/командная совместимость/production context/нехватка данных.
- [ ] Страница накопленной точности: число сверенных snapshots, MAE по поколениям/периодам/coverage bins.
- [ ] Каталог будущих релизов и последние прогнозы.
- [ ] Share/OG-картинка для конкретного snapshot.
- [ ] Публичный Adaptation Analyzer для уже вышедших экранизаций, когда качество автоматического StoryMap будет доказано.

## Порядок ближайших работ

1. **Production rollout текущего Vanga + jsint-site и retrain с актуальной схемой.**
2. **Завершить серверную валидацию P1 schema v6.**
3. **Creative Team: полный v6→v7→v8→v9→v10→v11→v12→v13→v14→v15 ablation; затем key-team aggregate/cohesion только отдельными инкрементами после проверки raw histories.**
4. **Production Context: наполнить factual registry studio/producer/franchise/events/consultancies и только затем исследовать temporal proxy features.**
5. **Text → StoryMap extractor для RU/EN summaries.**
6. **Пилот Expert Analysis Corpus: Красный Циник + BadComedian + blind validation.**
7. **Source/adaptation pre-release features, выведенные из ретроспективных закономерностей.**
8. **Накопление временной истории IMDb ratings.**
9. **Future-release discovery и дальнейший публичный UX.**

## Критерии готовности любого ML-инкремента

Новый блок считается готовым к публикации только если:

1. источник и дата данных воспроизводимы;
2. нет post-release leakage;
3. train/inference вычисляют признак одинаково;
4. missing/unknown состояние явно представлено;
5. есть тесты;
6. выполнена ablation/temporal evaluation;
7. качество не нарушает установленный quality gate;
8. укладывается в ресурсы production VPS;
9. документация обновлена;
10. в публичном UI не создаётся ложное ощущение точности или причинности.

## Связанные документы

- `README.md` — текущее состояние Vanga и эксплуатация.
- `docs/ADAPTATION_ANALYZER.md` — архитектура ретроспективного анализа адаптаций.
- `docs/DATA_COVERAGE.md` и `docs/P1_STATUS.md` — контракт и статус P1.
- `docs/P2_STATUS.md` — Creative Team schema v7-v15 и безопасные поэтапные ablation.
- `docs/PRODUCTION_CONTEXT.md` — factual registry, temporal/provenance contract, multi-director, studio/franchise/producer context и внешний creative influence.
- `docs/EXPERT_ANALYSIS_CORPUS.md` — многопрофильный экспертный корпус, blind validation и переносимые методы анализа.
- `docs/P9_TEMPORAL_FACTS.md` — temporal runtime/genres/synopsis, batch import и Wikidata facts collector.
- Этот `docs/ROADMAP.md` — источник истины по согласованным планам дальнейшего развития.