from src.kinovanga import KinoVanga
from settings import config

# Обучение (опционально)
kino = KinoVanga(f"{config.ABSPATH}/models/model.cbm")

# Предсказание
rating = kino.predict(
    title="Дюна",
    director="Denis Villeneuve",
    year=2021,
    runtime=155,
    genres="Adventure,Sci-Fi,Drama",
    actors=[
        "Timothée Chalamet",
        "Rebecca Ferguson",
        "Oscar Isaac"
    ],
    num_votes=850000
)

print(f"Рейтинг: {rating}")