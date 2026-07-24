import numpy as np
import joblib
import os
from sklearn.linear_model import SGDRegressor
from sklearn.impute import SimpleImputer
from sklearn.feature_extraction.text import HashingVectorizer
from sklearn.preprocessing import StandardScaler
from .db_utils import get_total_rows, fetch_chunk, fetch_sample

def train_imputer(conn, sample_size):
    df = fetch_sample(conn, sample_size)
    X_num = df[['startYear', 'runtimeMinutes']].values.astype(np.float64)
    imputer = SimpleImputer(strategy='median')
    imputer.fit(X_num)
    return imputer

def get_director_avg_dict(conn):
    query = """
        SELECT main_director, AVG(averageRating) AS avg_rating
        FROM movies
        WHERE main_director IS NOT NULL AND main_director != ''
        GROUP BY main_director
    """
    df = conn.execute(query).df()
    return dict(zip(df['main_director'], df['avg_rating']))

def train_model_incremental(conn, config):
    chunk_size = config['train']['chunk_size']
    model_dir = config['paths']['model_dir']
    os.makedirs(model_dir, exist_ok=True)
    
    # Импьютер для числовых (заполняем медианой)
    print("Обучение импьютера...")
    imputer = train_imputer(conn, config['train']['sample_for_imputer'])
    joblib.dump(imputer, os.path.join(model_dir, config['paths']['imputer_filename']))
    
    # Словарь режиссёров
    print("Загрузка средних рейтингов режиссёров...")
    director_avg = get_director_avg_dict(conn)
    joblib.dump(director_avg, os.path.join(model_dir, config['paths']['director_avg_filename']))
    global_avg = np.nanmean(list(director_avg.values())) if director_avg else 6.0
    print(f"Глобальный средний рейтинг: {global_avg:.2f}")
    
    # Векторизатор (HashingVectorizer с L2-нормой)
    vectorizer = HashingVectorizer(
        n_features=config['features']['hashing_vectorizer_n_features'],
        stop_words='english',
        lowercase=True,
        alternate_sign=False,
        norm='l2'   # нормализация текстовых векторов
    )
    joblib.dump(vectorizer, os.path.join(model_dir, config['paths']['vectorizer_filename']))
    
    # Модель
    model = SGDRegressor(**config['model']['sgd_params'])
    
    # Инициализируем StandardScaler для числовых признаков
    scaler = StandardScaler()
    scaler_fitted = False
    
    total_rows = get_total_rows(conn)
    print(f"Всего строк: {total_rows}")
    
    offset = 0
    first_batch = True
    processed = 0
    
    while True:
        chunk = fetch_chunk(conn, chunk_size, offset)
        if chunk.empty:
            break
        
        # Числовые признаки
        X_num = chunk[['startYear', 'runtimeMinutes']].values.astype(np.float64)
        X_num_imp = imputer.transform(X_num)
        
        # Масштабируем числовые признаки (через StandardScaler)
        if not scaler_fitted:
            scaler.fit(X_num_imp)   # обучаем на первом чанке
            scaler_fitted = True
        X_num_scaled = scaler.transform(X_num_imp)
        
        # Текстовые признаки
        texts = chunk['primaryTitle'].fillna('').astype(str).tolist()
        X_text = vectorizer.transform(texts).toarray()
        
        # Доп. признаки
        is_remake = chunk['is_remake'].values.reshape(-1, 1)
        dir_avg = chunk['director_avg_rating'].values.reshape(-1, 1)
        dir_avg = np.nan_to_num(dir_avg, nan=global_avg)
        
        # Объединяем все признаки
        X = np.hstack([X_num_scaled, X_text, is_remake, dir_avg])
        y = chunk['averageRating'].values.astype(np.float64)
        
        # Удаляем NaN в y
        mask = ~np.isnan(y)
        X, y = X[mask], y[mask]
        
        if len(y) == 0:
            offset += chunk_size
            continue
        
        if first_batch:
            model.partial_fit(X, y)
            first_batch = False
        else:
            model.partial_fit(X, y)
        
        processed += len(y)
        print(f"Обработано {processed} / {total_rows}")
        offset += chunk_size
    
    # Сохраняем scaler
    joblib.dump(scaler, os.path.join(model_dir, "scaler.pkl"))
    joblib.dump(model, os.path.join(model_dir, config['paths']['model_filename']))
    print("Модель сохранена.")
    return model, imputer, scaler, vectorizer, director_avg