from src.kinovanga import KinoVanga
from settings import config

# Обучение (опционально)
kino = KinoVanga(f"{config.ABSPATH}/models/model.cbm")

# Предсказание
rating = kino.predict(
    title="Superman",
    director="James Gunn",
    year=2025,
    runtime=129,
    genres="sci-fi,action",
    actors=[
        "David Corenswet",
        "Rachel Brosnahan",
        "Nicholas Hoult"
    ]
)

print(f"Рейтинг: {rating}")