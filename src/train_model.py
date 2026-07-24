import numpy as np
from sklearn.linear_model import SGDRegressor
from sklearn.preprocessing import StandardScaler

from src.data_filtr import get_batches
from src.logger import setup_logger

logger = setup_logger(__name__)


def train_model(all_genres: list, batch_size=10000):
    n_features = len(all_genres) + 2
    scaler = StandardScaler()
    model = SGDRegressor(
        loss='huber',
        penalty='l2',
        alpha=0.0001,
        learning_rate='constant',
        eta0=0.0001,
        max_iter=1,
        warm_start=True,
        random_state=42,
        tol=1e-3,
        early_stopping=False
    )
    first_batch = True
    total_rows = 0
    for X, y in get_batches(all_genres, batch_size):
        if len(X) == 0:
            continue
        X_np = X.values.astype(np.float64)
        y_np = y.values.astype(np.float64)
        # --- Стандартизация (один раз) ---
        if first_batch:
            scaler.partial_fit(X_np)
            first_batch = False
        else:
            scaler.partial_fit(X_np)
        X_scaled = scaler.transform(X_np)
        # --- Обучение ---
        model.partial_fit(X_scaled, y_np)
        total_rows += len(y_np)
        logger.info(f"Обработано строк: {total_rows}, текущие веса (первые 5): {model.coef_[:5]}")
    logger.info("Обучение завершено")
    return model, scaler

def interpret_model(model, scaler, feature_names):
    """
    Выводит коэффициенты модели в исходных единицах.
    Для числовых признаков учтено ручное масштабирование.
    """
    # Коэффициенты после учёта StandardScaler
    coef_scaled = model.coef_ / scaler.scale_
    coef_dict = dict(zip(feature_names, coef_scaled))
    
    # Поправка на ручное масштабирование для года и длительности
    if 'startYear' in coef_dict:
        coef_dict['startYear'] /= 100.0
    if 'runtimeMinutes' in coef_dict:
        coef_dict['runtimeMinutes'] /= 100.0
    # Для numVotes поправка не нужна — он уже в логарифмическом масштабе
    
    # Сортируем
    features_sorted = sorted(coef_dict.items(), key=lambda x: x[1], reverse=True)
    
    logger.info("=== Топ-10 положительных влияний (в исходных единицах) ===")
    for name, val in features_sorted[:10]:
        logger.info(f"{name}: {val:.4f}")
    
    logger.info("\n=== Топ-10 отрицательных влияний ===")
    for name, val in features_sorted[-10:]:
        logger.info(f"{name}: {val:.4f}")
    
    if 'startYear' in coef_dict:
        logger.info(f"\nВлияние года выпуска (на 1 год): {coef_dict['startYear']:.4f}")
    if 'runtimeMinutes' in coef_dict:
        logger.info(f"Влияние длительности (на 1 минуту): {coef_dict['runtimeMinutes']:.4f}")
    if 'numVotes' in coef_dict:
        logger.info(f"Влияние логарифма голосов (на 1 единицу log): {coef_dict['numVotes']:.4f}")