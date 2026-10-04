from __future__ import annotations

from src.creative_kinovanga import KinoVanga
from src.train_model import resolve_current_model_path


EXAMPLES = [
    {
        "title": "Superman",
        "directors": ["James Gunn"],
        "writer": "James Gunn",
        "year": 2025,
        "runtime": 129,
        "genres": ["sci-fi", "action"],
        "actors": ["David Corenswet", "Rachel Brosnahan", "Nicholas Hoult"],
    },
    {
        "title": "Supergirl",
        "directors": ["Craig Gillespie"],
        "year": 2026,
        "runtime": 108,
        "genres": ["sci-fi", "action"],
        "actors": ["Milly Alcock", "Matthias Schoenaerts", "Eve Ridley"],
    },
    {
        "title": "Spider-Man: Brand New Day",
        "directors": ["Destin Daniel Cretton"],
        "year": 2026,
        "runtime": 145,
        "genres": ["sci-fi", "action"],
        "actors": ["Tom Holland", "Zendaya", "Sadie Sink"],
    },
    {
        "title": "The Odyssey",
        "directors": ["Christopher Nolan"],
        "writer": "Christopher Nolan",
        "year": 2026,
        "runtime": 173,
        "genres": ["adventure", "drama"],
        "actors": ["Matt Damon", "Tom Holland", "Anne Hathaway"],
    },
]


def main() -> int:
    """Локальный пример использует тот же расширенный inference-класс, что API."""

    kino = KinoVanga(resolve_current_model_path())
    try:
        for example in EXAMPLES:
            directors = list(example["directors"])
            result = kino.predict(
                title=str(example["title"]),
                director=directors[0],
                directors=directors,
                writer=example.get("writer"),
                year=int(example["year"]),
                runtime=int(example["runtime"]),
                genres=example["genres"],
                actors=list(example["actors"]),
                explain=True,
            )
            print(f"{example['title']}: рейтинг {result['rating']}")
            print(f"Объяснение: {result['explanation']}")
    finally:
        kino.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
