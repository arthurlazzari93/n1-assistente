# app/kb.py
from __future__ import annotations

import json
import math
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

try:
    from loguru import logger
except Exception:  # fallback simples
    class _L:
        def info(self, *a, **k): print("[INFO]", *a)
        def warning(self, *a, **k): print("[WARN]", *a)
        def error(self, *a, **k): print("[ERROR]", *a)
    logger = _L()  # type: ignore

# Diretórios/arquivos
KB_DIR = Path(__file__).parent / "knowledge"
KB_INDEX = Path(__file__).parent / "kb_index.json"

# Parâmetros BM25 (Okapi)
K1 = 1.5
B = 0.75

# Boosts de campos
TITLE_BOOST = 3        # título pesa mais
TAGS_BOOST = 2         # tags ajudam recall
SYN_BOOST = 2          # sinônimos/aliases ajudam recall

# Estado em memória
_DOCS: List[Dict[str, Any]] = []          # [{id, path, title, tags, synonyms, text}]
_CHUNKS: List[Dict[str, Any]] = []        # [{id, doc_id, text, tokens, tf, len}]
_IDF: Dict[str, float] = {}
_AVGDL: float = 1.0
_DOC_BY_ID: Dict[int, Dict[str, Any]] = {}
_SYN_INDEX: Dict[str, set] = {}           # token -> {sinônimos}

# --------------------------------------------------------------------------------------
# Utils
# --------------------------------------------------------------------------------------

def _strip_accents(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch))

def _norm(text: str) -> str:
    text = _strip_accents(text.lower().strip())
    text = re.sub(r"\s+", " ", text)
    return text

def _tokenize(text: str) -> List[str]:
    return re.findall(r"[a-z0-9]+", _norm(text))

def _parse_frontmatter(raw: str) -> Tuple[Dict[str, Any], str]:
    if raw.startswith("---"):
        try:
            end = raw.index("\n---", 3)
            block = raw[3:end].strip()
            body = raw[end+4:].lstrip()
            meta: Dict[str, Any] = {}
            for line in block.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip().lower()] = v.strip()
            def _to_list(v: Any) -> List[str]:
                if isinstance(v, list):
                    return [str(x).strip() for x in v]
                if isinstance(v, str) and v.strip():
                    s = v.strip()
                    if s.startswith("[") and s.endswith("]"):
                        parts = [p.strip(" []") for p in s.split(",")]
                        return [p for p in parts if p]
                    if ";" in s:
                        return [p.strip() for p in s.split(";")]
                    if "," in s:
                        return [p.strip() for p in s.split(",")]
                    return [s]
                return []
            meta["tags"] = _to_list(meta.get("tags", []))
            meta["synonyms"] = _to_list(meta.get("synonyms", []))
            return meta, body
        except ValueError:
            pass
    return {}, raw

def _split_chunks(text: str, target_words: int = 120) -> List[str]:
    paras = [p.strip() for p in re.split(r"\n{2,}", text or "") if p.strip()]
    chunks, curr, count = [], [], 0
    for p in paras:
        w = len(p.split())
        if count + w > target_words and curr:
            chunks.append("\n\n".join(curr))
            curr, count = [], 0
        curr.append(p)
        count += w
    if curr:
        chunks.append("\n\n".join(curr))
    return chunks or ([text.strip()] if text else [])

def _ensure_kb_dir():
    KB_DIR.mkdir(parents=True, exist_ok=True)

# --------------------------------------------------------------------------------------
# Indexação
# --------------------------------------------------------------------------------------

def _build_index() -> None:
    global _DOCS, _CHUNKS, _IDF, _AVGDL, _DOC_BY_ID, _SYN_INDEX
    _DOCS, _CHUNKS = [], []
    _DOC_BY_ID, _SYN_INDEX = {}, {}
    next_doc_id, next_chunk_id = 0, 0

    _ensure_kb_dir()

    for path in sorted(KB_DIR.glob("*.md")):
        raw = path.read_text(encoding="utf-8", errors="ignore")
        meta, body = _parse_frontmatter(raw)
        title = meta.get("title") or path.stem.replace("_", " ").title()
        tags: List[str] = meta.get("tags") or []
        syns: List[str] = meta.get("synonyms") or []

        doc_id = next_doc_id; next_doc_id += 1
        doc = {"id": doc_id, "path": str(path), "title": title, "tags": tags, "synonyms": syns, "text": body}
        _DOCS.append(doc)
        _DOC_BY_ID[doc_id] = doc

        # índice de sinônimos global
        for ent in (syns + tags):
            for tok in _tokenize(ent):
                expanded = set(_tokenize(ent))
                if tok:
                    _SYN_INDEX.setdefault(tok, set()).update(expanded)

        # tokens de meta (para boosts)
        title_tokens = _tokenize(title)
        tag_tokens: List[str] = []
        for t in tags:
            tag_tokens.extend(_tokenize(t))
        syn_tokens: List[str] = []
        for s in syns:
            syn_tokens.extend(_tokenize(s))

        # chunking
        for ch_text in _split_chunks(body):
            tokens = _tokenize(ch_text)
            tf: Dict[str, int] = {}
            for t in tokens:
                tf[t] = tf.get(t, 0) + 1

            # boosts de meta
            for t in title_tokens:
                tf[t] = tf.get(t, 0) + TITLE_BOOST
            for t in tag_tokens:
                tf[t] = tf.get(t, 0) + TAGS_BOOST
            for t in syn_tokens:
                tf[t] = tf.get(t, 0) + SYN_BOOST

            _CHUNKS.append({
                "id": next_chunk_id,
                "doc_id": doc_id,
                "text": ch_text,
                "tf": tf,
                "len": max(1, sum(tf.values())),
            })
            next_chunk_id += 1

    # IDF/AVGDL
    N = len(_CHUNKS) or 1
    df: Dict[str, int] = {}
    for ch in _CHUNKS:
        for t in ch["tf"].keys():
            df[t] = df.get(t, 0) + 1
    _IDF = {t: math.log((N - df_t + 0.5) / (df_t + 0.5) + 1.0) for t, df_t in df.items()}
    _AVGDL = sum(ch["len"] for ch in _CHUNKS) / (len(_CHUNKS) or 1)

    try:
        KB_INDEX.write_text(json.dumps({
            "docs": len(_DOCS), "chunks": len(_CHUNKS), "avgdl": _AVGDL
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"[KB] não consegui gravar índice resumido: {e}")

def reindex() -> Dict[str, Any]:
    _build_index()
    return {"docs": len(_DOCS), "chunks": len(_CHUNKS), "avgdl": _AVGDL}


def rebuild_kb_index() -> Dict[str, Any]:
    """
    Alias público para reconstruir o índice da KB a partir dos arquivos markdown.
    """
    return reindex()

# --------------------------------------------------------------------------------------
# Busca (BM25 + priors opcionais)
# --------------------------------------------------------------------------------------

def _expand_query_tokens(q_tokens: List[str]) -> List[str]:
    out: List[str] = []
    for t in q_tokens:
        out.append(t)
        syns = _SYN_INDEX.get(t)
        if syns:
            out.extend(list(syns))
    # dedup mantendo ordem
    seen, dedup = set(), []
    for x in out:
        if x not in seen:
            dedup.append(x); seen.add(x)
    return dedup

def _bm25_score_from_tokens(q_tokens: Iterable[str], ch: Dict[str, Any]) -> float:
    score = 0.0
    dl = ch["len"]
    for q in q_tokens:
        tf = ch["tf"].get(q)
        if not tf:
            continue
        idf = _IDF.get(q, 0.0)
        denom = tf + K1 * (1 - B + B * dl / _AVGDL)
        score += idf * (tf * (K1 + 1)) / (denom if denom else 1.0)
    return score

def _bm25_score(query: str, ch: Dict[str, Any]) -> float:
    q_tokens = _expand_query_tokens(_tokenize(query))
    return _bm25_score_from_tokens(q_tokens, ch)

def search(
    query: str,
    k: int = 5,
    threshold: float = 1.5,
    priors: Dict[str, float] | None = None,
    alpha: float = 0.3,
) -> List[Dict[str, Any]]:
    """
    Retorna os melhores trechos da KB para a 'query'.
    - Usa BM25 (com boosts de meta) e aplica 'priors' preditivos por documento:
      score_final = bm25 * (1 + alpha * prior_doc)   | prior ~ [-1 .. +1]
    - Filtra por 'threshold' no score_final.
    - Saída: lista ordenada por 'score' decrescente com metadados.
    """
    if not _CHUNKS:
        return []
    priors = priors or {}

    scored: List[Tuple[float, Dict[str, Any], float, float]] = []
    for ch in _CHUNKS:
        bm25 = _bm25_score(query, ch)
        doc = _DOC_BY_ID[ch["doc_id"]]
        prior = float(priors.get(doc["path"], 0.0))
        score_final = bm25 * (1.0 + alpha * prior)
        if score_final >= threshold:
            scored.append((score_final, ch, bm25, prior))

    scored.sort(key=lambda x: x[0], reverse=True)
    out: List[Dict[str, Any]] = []
    for score_final, ch, bm25, prior in scored[:k]:
        doc = _DOC_BY_ID[ch["doc_id"]]
        out.append({
            "score": round(float(score_final), 4),
            "bm25": round(float(bm25), 4),
            "prior": round(float(prior), 4),
            "doc_id": doc["id"],
            "doc_title": doc["title"],
            "doc_path": doc["path"],
            "chunk_text": ch["text"],
        })
    return out

# --------------------------------------------------------------------------------------
# Fallback simples para resposta direta (mantido para compatibilidade)
# --------------------------------------------------------------------------------------

def _merge_top_by_doc(hits: List[Dict[str, Any]], max_docs: int = 3) -> List[Dict[str, Any]]:
    grouped: Dict[str, Dict[str, Any]] = {}
    for h in hits:
        key = h["doc_path"]
        if key not in grouped:
            grouped[key] = h
    return list(grouped.values())[:max_docs]

def kb_try_answer(query: str, threshold: float = 2.5, priors: Dict[str, float] | None = None) -> Dict[str, Any] | None:
    """
    Usa a própria KB para montar uma resposta curta quando não há LLM.
    """
    hits = search(query, k=8, threshold=threshold, priors=priors)
    if not hits:
        return None
    top = _merge_top_by_doc(hits, max_docs=3)
    parts = []
    for h in top:
        snippet = h["chunk_text"].strip()
        parts.append(f"**{h['doc_title']}**\n{snippet}")
    reply = (
        "Encontrei isto na nossa base de conhecimento:\n\n" +
        "\n\n---\n\n".join(parts) +
        "\n\nSe precisar, posso detalhar mais ou seguir com os próximos passos."
    )
    return {"reply": reply, "sources": [{"title": h["doc_title"], "path": h["doc_path"], "score": h["score"]} for h in top]}

# --------------------------------------------------------------------------------------
# Inicialização
# --------------------------------------------------------------------------------------

try:
    stats = reindex()
    logger.info(f"[KB] indexado: {stats}")
except Exception as e:
    logger.error(f"[KB] falha ao indexar: {e}")
