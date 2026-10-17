from datetime import datetime

from pydantic import BaseModel, Field, field_validator

# Keyword-Grenzen: ein leeres oder 1-Zeichen-Keyword ("", " ", "a") matcht
# per Substring-Suche praktisch jedes Dokument und kapert die Scope-Zuweisung.
KEYWORD_MIN_LENGTH = 2
KEYWORD_MAX_LENGTH = 100
KEYWORDS_MAX_COUNT = 50
COLOR_PATTERN = r"^#[0-9A-Fa-f]{6}$"


def _clean_name(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        raise ValueError("Name darf nicht leer sein")
    return value


def _clean_keywords(value: list[str] | None) -> list[str] | None:
    if value is None:
        return None
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in value:
        keyword = " ".join(str(raw).split())
        if not keyword:
            continue
        if len(keyword) < KEYWORD_MIN_LENGTH:
            raise ValueError(
                f"Schlüsselwort '{keyword}' ist zu kurz (mindestens {KEYWORD_MIN_LENGTH} Zeichen)"
            )
        if len(keyword) > KEYWORD_MAX_LENGTH:
            raise ValueError(
                f"Schlüsselwort ist zu lang (höchstens {KEYWORD_MAX_LENGTH} Zeichen)"
            )
        key = keyword.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(keyword)
    if len(cleaned) > KEYWORDS_MAX_COUNT:
        raise ValueError(f"Höchstens {KEYWORDS_MAX_COUNT} Schlüsselwörter erlaubt")
    return cleaned


class FilingScopeCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    keywords: list[str] = []
    is_default: bool = False
    color: str | None = Field(None, pattern=COLOR_PATTERN)

    _name = field_validator("name")(_clean_name)
    _keywords = field_validator("keywords")(_clean_keywords)


class FilingScopeUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None
    keywords: list[str] | None = None
    is_default: bool | None = None
    color: str | None = Field(None, pattern=COLOR_PATTERN)

    _name = field_validator("name")(_clean_name)
    _keywords = field_validator("keywords")(_clean_keywords)


class FilingScopeResponse(BaseModel):
    model_config = {"from_attributes": True}
    id: str
    name: str
    slug: str
    description: str | None = None
    keywords: list[str] = []
    is_default: bool
    color: str | None = None
    created_at: datetime
