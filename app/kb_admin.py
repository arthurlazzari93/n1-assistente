from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple, Union

from app import kb
from app.schemas import (
    KBArticle,
    KBArticleCreate,
    KBArticleMetadata,
    KBArticleUpdate,
)

WritePayload = Union[KBArticleCreate, KBArticleUpdate]


class KBArticleNotFoundError(Exception):
    """Artigo solicitado não existe."""


class KBArticleAlreadyExistsError(Exception):
    """Já existe artigo com o mesmo slug."""


def _kb_dir() -> Path:
    path = kb.KB_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def _slug_to_path(slug: str) -> Path:
    safe = slug.strip().lower()
    return _kb_dir() / f"{safe}.md"


def _to_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"0", "false", "no", "nao", "não"}:
        return False
    if text in {"1", "true", "yes", "sim"}:
        return True
    return default


def _format_list(items: Iterable[str]) -> str:
    cleaned = [str(item).strip() for item in items if str(item).strip()]
    if not cleaned:
        return "[]"
    return "[" + ", ".join(cleaned) + "]"


def _serialize_frontmatter(data: WritePayload, extras: Dict[str, Any] | None = None) -> str:
    extras = extras or {}
    lines = [
        "---",
        f"title: {data.titulo}",
        f"tags: {_format_list(data.tags)}",
        f"active: {'true' if data.ativo else 'false'}",
    ]
    for key in sorted(extras.keys()):
        if key in {"title", "tags", "active"}:
            continue
        value = extras[key]
        if isinstance(value, (list, tuple)):
            rendered = _format_list(value)
        else:
            rendered = str(value)
        lines.append(f"{key}: {rendered}")
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def _split_article(path: Path) -> Tuple[Dict[str, Any], str]:
    raw = path.read_text(encoding="utf-8")
    meta, body = kb._parse_frontmatter(raw)  # type: ignore[attr-defined]
    return meta or {}, body or ""


def _article_from_path(path: Path, include_content: bool) -> Tuple[Dict[str, Any], Union[KBArticleMetadata, KBArticle]]:
    meta, body = _split_article(path)
    slug = path.stem
    titulo = meta.get("title") or slug.replace("_", " ").title()
    tags = meta.get("tags") or []
    ativo = _to_bool(meta.get("active"), True)
    payload: Dict[str, Any] = {
        "id": slug,
        "slug": slug,
        "titulo": titulo,
        "tags": tags,
        "ativo": ativo,
    }
    if include_content:
        content = body.rstrip()
        if content:
            content += "\n"
        payload["conteudo_markdown"] = content
        return meta, KBArticle(**payload)
    return meta, KBArticleMetadata(**payload)


def list_kb_articles() -> List[KBArticleMetadata]:
    articles: List[KBArticleMetadata] = []
    for path in sorted(_kb_dir().glob("*.md")):
        if not path.is_file():
            continue
        _, payload = _article_from_path(path, include_content=False)
        articles.append(payload)  # type: ignore[arg-type]
    return articles


def get_kb_article(slug: str) -> KBArticle:
    path = _slug_to_path(slug)
    if not path.exists():
        raise KBArticleNotFoundError(slug)
    _, payload = _article_from_path(path, include_content=True)
    return payload  # type: ignore[return-value]


def create_kb_article(data: KBArticleCreate) -> KBArticle:
    path = _slug_to_path(data.slug)
    if path.exists():
        raise KBArticleAlreadyExistsError(data.slug)
    _write_article(path, data, extras=None)
    return get_kb_article(data.slug)


def update_kb_article(slug: str, data: KBArticleUpdate) -> KBArticle:
    if data.slug != slug:
        raise ValueError("slug do payload precisa corresponder ao slug da rota.")
    path = _slug_to_path(slug)
    if not path.exists():
        raise KBArticleNotFoundError(slug)
    meta, _ = _article_from_path(path, include_content=False)
    extra_meta = {k: v for k, v in meta.items() if k not in {"title", "tags", "active"}}
    _write_article(path, data, extras=extra_meta)
    return get_kb_article(slug)


def force_reindex() -> Dict[str, Any]:
    """
    Reconstrói o índice BM25 consumido pela triagem.
    """
    return kb.reindex()


def _write_article(path: Path, data: WritePayload, extras: Dict[str, Any] | None = None) -> None:
    frontmatter = _serialize_frontmatter(data, extras)
    body = (data.conteudo_markdown or "").rstrip()
    if body:
        body += "\n"
    text = frontmatter + body
    path.write_text(text, encoding="utf-8")
