import pandas as pd
import numpy as np
from sklearn.metrics import mean_absolute_error, r2_score

def evaluate_model(model, X_test, y_test, imputer):
    """Вычисляет MAE и R² на тестовых данных."""
    X_test_imp = imputer.transform(X_test)
    y_pred = model.predict(X_test_imp)
    # Убираем NaN в y_test (если есть)
    mask = ~np.isnan(y_test)
    y_test_clean = y_test[mask]
    y_pred_clean = y_pred[mask]
    if len(y_test_clean) == 0:
        return None, None
    return mean_absolute_error(y_test_clean, y_pred_clean), r2_score(y_test_clean, y_pred_clean)

def get_sample_comparison(model, X_test, y_test, df_original, imputer, num_samples=5, random_state=42):
    """
    Возвращает DataFrame со сравнением предсказаний и реальных рейтингов для случайных фильмов.
    """
    X_test_imp = imputer.transform(X_test)
    y_pred = model.predict(X_test_imp)
    # Фильтруем NaN
    mask = ~np.isnan(y_test)
    indices = np.where(mask)[0]
    if len(indices) == 0:
        return pd.DataFrame()

    np.random.seed(random_state)
    chosen = np.random.choice(indices, size=min(num_samples, len(indices)), replace=False)

    results = []
    for idx in chosen:
        # Находим оригинальную строку по индексу (помним, что X_test.index совпадает с df)
        original_row = df_original.loc[X_test.index[idx]]
        results.append({
            'Название': original_row['primaryTitle'],
            'Год': int(original_row['startYear']),
            'Режиссёр': original_row['directors'],
            'Длительность (мин)': int(original_row['runtimeMinutes']),
            'Предсказанный рейтинг': round(y_pred[idx], 2),
            'Реальный рейтинг': round(y_test.iloc[idx], 2),
            'Ошибка': round(abs(y_pred[idx] - y_test.iloc[idx]), 2)
        })
    return pd.DataFrame(results)