from __future__ import annotations

import re
from typing import List

from pydantic import BaseModel, Field, field_validator

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,64}$")


class KBArticleBase(BaseModel):
    """
    Representa metadados básicos de um artigo de KB.
    O campo 'slug' é usado como nome do arquivo em app/knowledge/{slug}.md.
    """

    slug: str = Field(..., description="Identificador único (usado para o nome do arquivo .md).")
    titulo: str = Field(..., description="Título exibido do artigo.")
    tags: List[str] = Field(default_factory=list, description="Lista de tags/keywords.")
    ativo: bool = Field(default=True, description="Indica se o artigo está ativo na KB.")

    @field_validator("slug")
    @classmethod
    def _validate_slug(cls, value: str) -> str:
        slug = (value or "").strip().lower()
        if not _SLUG_RE.match(slug):
            raise ValueError("slug deve conter apenas letras minúsculas, números, '-' ou '_'")
        return slug

    @field_validator("tags", mode="before")
    @classmethod
    def _ensure_tags(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        return [str(tag).strip() for tag in list(value) if str(tag).strip()]


class KBArticleCreate(KBArticleBase):
    conteudo_markdown: str = Field(..., description="Conteúdo principal em Markdown.")


class KBArticleUpdate(KBArticleBase):
    conteudo_markdown: str = Field(..., description="Conteúdo principal em Markdown.")


class KBArticleMetadata(KBArticleBase):
    id: str = Field(..., description="Identificador interno do artigo (slug).")


class KBArticle(KBArticleMetadata):
    conteudo_markdown: str = Field(..., description="Conteúdo principal em Markdown.")
