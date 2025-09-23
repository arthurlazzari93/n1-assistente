# app/learning.py
from __future__ import annotations
import json
import os
import threading
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

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
