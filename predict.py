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

rating = kino.predict(
    title="Supergirl",
    director="James Gunn",
    year=2026,
    runtime=108,
    genres="sci-fi,action",
    actors=[
        "Milly Alcock",
        "Matthias Schoenaerts",
        "Eve Ridley"
    ]
)
print(f"Рейтинг: {rating}")

rating = kino.predict(
    title="Spider-Man: Brand New Day",
    director="Destin Daniel Cretton",
    year=2026,
    runtime=145,
    genres="sci-fi,action",
    actors=[
        "Tom Holland",
        "Zendaya",
        "Sadie Sink"
    ]
)
print(f"Рейтинг: {rating}")

rating = kino.predict(
    title="The Odyssey",
    director="Christopher Nolan",
    year=2026,
    runtime=173,
    genres="sci-fi,action",
    actors=[
        "Matt Damon",
        "Tom Holland",
        "Anne Hathaway"
    ]
)
print(f"Рейтинг: {rating}")