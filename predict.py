from src.kinovanga import KinoVanga
from settings import config

# Обучение (опционально)
kino = KinoVanga(f"{config.ABSPATH}/models/model.cbm")

# Предсказание
rating = kino.predict_rating(
    title="Дюна",
    director="Дени Вильнёв",
    year=2021,
    runtime=155,
    description="Эпическая космическая сага о борьбе за ресурсы..."
)