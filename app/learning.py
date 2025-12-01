# app/learning.py
from __future__ import annotations
import json
import os
import threading
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    from loguru import logger
except Exception:  # fallback simples se loguru não estiver disponível
    class _L:
        def info(self, *a, **k): print("[INFO]", *a)
        def warning(self, *a, **k): print("[WARN]", *a)
        def error(self, *a, **k): print("[ERROR]", *a)
    logger = _L()  # type: ignore


# === Configuração de armazenamento ===
DATA_DIR = Path(os.getenv("N1_DATA_DIR", Path(__file__).parent / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
STORE_FILE = DATA_DIR / "feedback_kb.jsonl"   # append-only (uma linha por evento)


# === Modelo de dados ===
@dataclass
class FeedbackEvent:
    ts: str                # ISO UTC
    doc_path: str          # caminho do documento KB usado na resposta
    success: bool          # True = usuário respondeu "Sim", False = "Não"
    intent: Optional[str]  # ex.: "signature.generate", "signature.configure"
    ticket_id: Optional[str]  # id do chamado (para auditoria/consulta)
    user_hash: Optional[str]  # hash opcional do usuário (privacidade)

    @staticmethod
    def now_utc() -> str:
        return datetime.now(timezone.utc).isoformat()


_LOCK = threading.Lock()


def _success_rate(success: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(float(success) / float(total), 4)


# === Funções utilitárias de IO ===
def _append_event(ev: FeedbackEvent) -> None:
    line = json.dumps(asdict(ev), ensure_ascii=False)
    with _LOCK:
        with STORE_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def _iter_events() -> Iterable[FeedbackEvent]:
    if not STORE_FILE.exists():
        return []
    with _LOCK:
        with STORE_FILE.open("r", encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    d = json.loads(ln)
                    yield FeedbackEvent(
                        ts=d.get("ts"),
                        doc_path=d.get("doc_path", ""),
                        success=bool(d.get("success", False)),
                        intent=d.get("intent"),
                        ticket_id=d.get("ticket_id"),
                        user_hash=d.get("user_hash"),
                    )
                except Exception:
                    continue


# === API pública: gravação de feedback ===
def record_feedback(
    doc_path: str,
    success: bool,
    intent: Optional[str] = None,
    ticket_id: Optional[str] = None,
    user_hash: Optional[str] = None,
) -> None:
    """
    Registra um evento de feedback. Chame isso quando o usuário responder "Sim" / "Não".
    """
    if not doc_path:
        logger.warning("[learning] record_feedback chamado sem doc_path")
        return
    ev = FeedbackEvent(
        ts=FeedbackEvent.now_utc(),
        doc_path=doc_path,
        success=bool(success),
        intent=intent,
        ticket_id=str(ticket_id) if ticket_id is not None else None,
        user_hash=user_hash,
    )
    _append_event(ev)
    logger.info(f"[learning] feedback registrado: doc={doc_path} success={success} intent={intent}")


# === Cálculo de priors (preditivo) ===
def _exp_weight(age_days: float, half_life_days: float) -> float:
    """
    Peso exponencial decrescente por 'meia-vida'. Ex.: half_life_days=90
    => um evento com 90 dias vale 0.5 do peso de um evento de hoje.
    """
    if half_life_days <= 0:
        return 1.0
    return 0.5 ** (age_days / half_life_days)


def _age_days(ts_iso: str) -> float:
    try:
        t = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - t
        return max(0.0, delta.total_seconds() / 86400.0)
    except Exception:
        return 0.0


def _aggregate(events: Iterable[FeedbackEvent],
               intent: Optional[str],
               half_life_days: float) -> Dict[str, Tuple[float, float]]:
    """
    Retorna {doc_path: (wins_weighted, fails_weighted)}
    Filtra por intent se fornecida.
    """
    agg: Dict[str, Tuple[float, float]] = {}
    for ev in events:
        if intent and (ev.intent != intent):
            continue
        age = _age_days(ev.ts or "")
        w = _exp_weight(age, half_life_days)
        wins, fails = agg.get(ev.doc_path, (0.0, 0.0))
        if ev.success:
            wins += w
        else:
            fails += w
        agg[ev.doc_path] = (wins, fails)
    return agg


def get_priors(
    intent: Optional[str] = None,
    half_life_days: float = 90.0,
    m: float = 10.0,
) -> Dict[str, float]:
    """
    Calcula 'priors' por documento (e opcionalmente por 'intent'), no intervalo ~[-1, +1],
    onde valores positivos indicam maior taxa histórica de sucesso.

    Fórmula: prior = (wins - fails) / (wins + fails + m)
    - 'm' é suavização (quanto maior, mais conservador).
    - 'half_life_days' dá mais peso aos eventos recentes.
    """
    events = list(_iter_events())
    agg = _aggregate(events, intent=intent, half_life_days=half_life_days)
    priors: Dict[str, float] = {}
    for doc_path, (wins, fails) in agg.items():
        denom = wins + fails + max(1e-6, m)
        prior = (wins - fails) / denom
        # clamp por segurança numérica
        prior = max(-1.0, min(1.0, float(prior)))
        priors[doc_path] = prior
    return priors


def get_global_stats(half_life_days: float = 90.0) -> Dict[str, float]:
    """
    Estatísticas simples para debug/observabilidade.
    Retorna totais ponderados e taxa de sucesso global.
    """
    events = list(_iter_events())
    agg = _aggregate(events, intent=None, half_life_days=half_life_days)
    tw, tf = 0.0, 0.0
    for _, (w, f) in agg.items():
        tw += w; tf += f
    total = tw + tf
    rate = (tw / total) if total > 0 else 0.0
    return {
        "weighted_successes": round(tw, 3),
        "weighted_failures": round(tf, 3),
        "weighted_total": round(total, 3),
        "success_rate": round(rate, 4),
        "events_count": len(events),
    }


def get_feedback_metrics(
    top_docs: int = 5,
    max_recent: int = 30,
    half_life_days: float = 90.0,
    m: float = 10.0,
) -> Dict[str, Any]:
    """
    Consolida????o de feedbacks para o endpoint /debug/metrics.
    Retorna resumo global, por intent, ranking de documentos e eventos recentes.
    """
    events = list(_iter_events())
    total_events = len(events)
    success_total = sum(1 for ev in events if ev.success)
    failure_total = total_events - success_total

    last_event_ts: Optional[str] = None
    intent_totals: Dict[str, Dict[str, int]] = {}
    doc_totals: Dict[str, Dict[str, Any]] = {}
    weighted_by_doc = _aggregate(events, intent=None, half_life_days=half_life_days)

    for ev in events:
        intent_key = ev.intent or "unknown"
        intent_entry = intent_totals.setdefault(intent_key, {"total": 0, "success": 0, "failure": 0})
        intent_entry["total"] += 1
        if ev.success:
            intent_entry["success"] += 1
        else:
            intent_entry["failure"] += 1

        doc_key = ev.doc_path or ""
        doc_entry = doc_totals.setdefault(
            doc_key,
            {"total": 0, "success": 0, "failure": 0, "last_ts": None, "intents": {}},
        )
        doc_entry["total"] += 1
        if ev.success:
            doc_entry["success"] += 1
        else:
            doc_entry["failure"] += 1
        doc_entry["intents"][intent_key] = doc_entry["intents"].get(intent_key, 0) + 1
        if ev.ts and (doc_entry["last_ts"] is None or ev.ts > doc_entry["last_ts"]):
            doc_entry["last_ts"] = ev.ts
        if ev.ts and (last_event_ts is None or ev.ts > last_event_ts):
            last_event_ts = ev.ts

    tw = sum(w for w, _ in weighted_by_doc.values())
    tf = sum(f for _, f in weighted_by_doc.values())
    weighted_total = tw + tf
    weighted_stats = {
        "weighted_successes": round(tw, 3),
        "weighted_failures": round(tf, 3),
        "weighted_total": round(weighted_total, 3),
        "success_rate": round((tw / weighted_total) if weighted_total else 0.0, 4),
        "events_count": total_events,
        "half_life_days": half_life_days,
        "m": m,
    }

    intent_summary: List[Dict[str, Any]] = []
    for key, stats in sorted(intent_totals.items(), key=lambda kv: (kv[0] or "")):
        intent_summary.append(
            {
                "intent": None if key == "unknown" else key,
                "label": key if key != "unknown" else "unknown",
                "total": stats["total"],
                "success": stats["success"],
                "failure": stats["failure"],
                "success_rate": _success_rate(stats["success"], stats["total"]),
            }
        )

    doc_rows: List[Dict[str, Any]] = []
    for doc_key, stats in doc_totals.items():
        wins_weighted, fails_weighted = weighted_by_doc.get(doc_key, (0.0, 0.0))
        denom = wins_weighted + fails_weighted + max(1e-6, m)
        prior = (wins_weighted - fails_weighted) / denom if denom else 0.0
        prior = max(-1.0, min(1.0, float(prior)))
        intents_breakdown = sorted(stats["intents"].items(), key=lambda kv: kv[1], reverse=True)
        doc_rows.append(
            {
                "doc_path": doc_key or "(unknown)",
                "total": stats["total"],
                "success": stats["success"],
                "failure": stats["failure"],
                "success_rate": _success_rate(stats["success"], stats["total"]),
                "last_feedback": stats["last_ts"],
                "top_intent": intents_breakdown[0][0] if intents_breakdown else None,
                "intents": [
                    {
                        "intent": name if name != "unknown" else None,
                        "label": name if name != "unknown" else "unknown",
                        "count": count,
                    }
                    for name, count in intents_breakdown
                ],
                "prior": round(prior, 4),
            }
        )

    top_n = max(0, int(top_docs))
    top_positive = (
        sorted(doc_rows, key=lambda row: (row["success_rate"], row["total"]), reverse=True)[:top_n]
        if doc_rows and top_n
        else []
    )
    top_negative = (
        sorted(doc_rows, key=lambda row: (row["success_rate"], -row["total"]))[:top_n]
        if doc_rows and top_n
        else []
    )

    recent_sorted = sorted(events, key=lambda ev: ev.ts or "", reverse=True)
    recent_limit = max(0, int(max_recent))
    recent_events = [
        {
            "ts": ev.ts,
            "doc_path": ev.doc_path,
            "success": ev.success,
            "intent": ev.intent,
            "ticket_id": ev.ticket_id,
            "user_hash": ev.user_hash,
        }
        for ev in recent_sorted[:recent_limit]
    ]

    global_summary = {
        "total_events": total_events,
        "success": success_total,
        "failure": failure_total,
        "success_rate": _success_rate(success_total, total_events),
        "last_event_ts": last_event_ts,
        "unique_docs": len(doc_rows),
        "weighted": weighted_stats,
    }

    return {
        "global": global_summary,
        "by_intent": intent_summary,
        "documents": {
            "total": len(doc_rows),
            "top_positive": top_positive,
            "top_negative": top_negative,
        },
        "recent_events": recent_events,
    }

# === Utilidades administrativas (opcionais) ===
def export_events() -> List[dict]:
    """Exporta todos os eventos em memória (lista de dicionários)."""
    return [asdict(ev) for ev in _iter_events()]


def reset_feedback() -> None:
    """Apaga o arquivo de feedback. Use com cuidado."""
    if STORE_FILE.exists():
        with _LOCK:
            STORE_FILE.unlink(missing_ok=True)
        logger.warning("[learning] feedback resetado (arquivo removido)")
