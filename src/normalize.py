def normalize_single_genre(genre: str) -> str:
    """Приводит один жанр к каноническому виду: 'Sci-Fi', 'Action'."""
    if not genre:
        return ""
    g = genre.strip().lower()
    # Маппинг синонимов (можно расширять)
    mapping = {
        "sci fi": "Sci-Fi",
        "science fiction": "Sci-Fi",
        "romcom": "Romance",
        "romantic comedy": "Romance",
    }
    if g in mapping:
        return mapping[g]
    # Разбиваем по дефису, каждую часть делаем title
    parts = g.split('-')
    return '-'.join(part.title() for part in parts if part)

def normalize_genre_str(genre_str: str) -> str:
    """Нормализует строку с жанрами, разделёнными запятыми."""
    if not genre_str:
        return "Unknown"
    genres = [g.strip() for g in genre_str.split(',') if g.strip()]
    normalized = [normalize_single_genre(g) for g in genres]
    return ",".join(normalized) if normalized else "Unknown"

    
import re

FRANCHISE_KEYWORDS = {
    'marvel': ['marvel', 'avengers', 'iron man', 'captain america', 'thor', 'guardians', 'black panther', 'doctor strange', 'spider-man', 'ant-man', 'eternals', 'shang-chi'],
    'dc': ['batman', 'superman', 'wonder woman', 'justice league', 'aquaman', 'shazam', 'suicide squad', 'joker', 'the flash', 'green lantern'],
    'star_wars': ['star wars', 'clone wars', 'rogue one', 'solo'],
    'harry_potter': ['harry potter', 'fantastic beasts'],
    'bond': ['james bond', '007', 'casino royale', 'skyfall', 'spectre', 'no time to die']
}

def extract_title_features(title: str) -> dict:
    if not title:
        return {}
    title_lower = title.lower()
    features = {}
    # Франшизы
    for franchise, keywords in FRANCHISE_KEYWORDS.items():
        features[f'is_{franchise}'] = int(any(kw in title_lower for kw in keywords))
    # Длина
    features['title_len'] = len(title)
    features['title_word_count'] = len(title.split())
    # Наличие цифр
    features['has_digit'] = int(bool(re.search(r'\d', title)))
    # Наличие двоеточия (часто у сиквелов)
    features['has_colon'] = int(':' in title)
    return features