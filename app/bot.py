# app/bot.py
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from botbuilder.core import (
    ActivityHandler,
    TurnContext,
    ConversationState,
    StatePropertyAccessor,
    MemoryStorage,
)
from botbuilder.schema import ChannelAccount
from loguru import logger

from .movidesk_client import get_ticket_text_bundle
from .kb import kb_try_answer
from .ai.triage_agent import triage_next  # agente com intenção + priors + reranker
from .learning import record_feedback, get_priors  # feedback preditivo
from .ai.prompt_builder import build_initial_prompt
from .db import (
    set_user_current_ticket,
    get_user_context,
    get_user_context_by_teams_id,
    list_tickets_for_requester,
    get_ticket_rec,
)

# Nota de arquitetura:
#   Historicamente o bot assumia um único ticket por conversa. Esta implementação mantém compatibilidade,
#   mas adiciona suporte a múltiplos tickets por usuário (listar/continuar/status) e integrações com follow-ups
#   para ajustar automaticamente o contexto quando uma mensagem proativa é enviada.


def format_ticket_listing(tickets: List[Dict[str, Any]]) -> str:
    if not tickets:
        return "Não encontrei chamados em andamento para você."
    lines = ["Seus tickets mais recentes:"]
    for idx, ticket in enumerate(tickets, start=1):
        subject = ticket.get("subject") or f"Ticket #{ticket.get('ticket_id')}"
        lines.append(f"{idx}) #{ticket.get('ticket_id')} • {subject}")
    lines.append("Use `continuar <n>` para escolher um ticket ou digite `status` para ver o atual.")
    return "\n".join(lines)


def resolve_ticket_choice(choice: str, tickets: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not tickets:
        return None
    try:
        idx = int(choice)
        if 1 <= idx <= len(tickets):
            return tickets[idx - 1]
    except Exception:
        pass
    for ticket in tickets:
        if str(ticket.get("ticket_id")) == choice:
            return ticket
    return None


def build_status_message(ticket: Dict[str, Any]) -> str:
    subject = ticket.get("subject") or f"Ticket #{ticket.get('ticket_id')}"
    reason = ticket.get("n1_reason") or "Sem detalhes de classificação."
    status_bits = []
    if ticket.get("teams_notified"):
        status_bits.append("notificado no Teams")
    if ticket.get("allowed"):
        status_bits.append("fluxo ativo")
    else:
        status_bits.append("fora do escopo N1")
    status_line = ", ".join(status_bits)
    return f"Ticket #{ticket.get('ticket_id')} • {subject}\n{reason}\nStatus: {status_line}."


class N1Bot(ActivityHandler):
    """
    Bot N1 com fluxo orientado por IA:
      - comando 'status'
      - comando 'iniciar <ticket>'
      - confirma sempre com "Funcionou? Sim/Não", e encerra/escala conforme resposta
      - limite de 25 mensagens do agente por conversa
      - usa IA (triage_next) + KB como apoio quando fizer sentido
      - registra feedback de sucesso/fracasso para aprendizado contínuo

    Evolução (multi-ticket):
      - Comandos 'listar' / 'continuar <ticket>' permitem alternar rapidamente entre tickets do mesmo usuário.
      - O contexto atual também é atualizado pelos follow-ups proativos para que respostas "Sim/Não" funcionem fora da sessão original.
    """

    YES_TOKENS = {"sim", "deu certo", "funcionou", "resolvido", "pode encerrar"}
    NO_TOKENS = {
        "nao",
        "não",
        "ainda não",
        "nao deu",
        "não deu",
        "deu erro",
        "não funcionou",
        "nao funcionou",
    }

    def __init__(self, conversation_state: Optional[ConversationState] = None) -> None:
        if conversation_state is None:
            conversation_state = ConversationState(MemoryStorage())
        self.conversation_state: ConversationState = conversation_state
        self.conv_accessor: StatePropertyAccessor = conversation_state.create_property("conv")

    # ---------------- util ----------------
    async def _save(self, turn_context: TurnContext, conv: Dict[str, Any]) -> None:
        await self.conv_accessor.set(turn_context, conv)  # type: ignore
        await self.conversation_state.save_changes(turn_context)

    def _is_stuck(self, text: str) -> bool:
        t = (text or "").lower()
        gatilhos = [
            "nao achei",
            "não achei",
            "nao encontro",
            "não encontro",
            "nao aparece",
            "não aparece",
            "nao funciona",
            "não funciona",
            "nao deu certo",
            "não deu certo",
            "nao estou achando",
            "não estou achando",
        ]
        return any(g in t for g in gatilhos)

    def _user_says_yes(self, text: str) -> bool:
        t = (text or "").lower().strip()
        return any(tok == t or tok in t for tok in self.YES_TOKENS)

    def _user_says_no(self, text: str) -> bool:
        t = (text or "").lower().strip()
        return any(tok == t or tok in t for tok in self.NO_TOKENS)

    def _build_kb_query_text(self, ticket_ctx: Dict[str, Any], conv: Dict[str, Any]) -> str:
        subject = (ticket_ctx.get("subject") or "").strip()
        first_action = (ticket_ctx.get("first_action_text") or "").strip()
        last_user = ""
        for msg in reversed(conv.get("hist", [])):
            if (msg.get("role") or "").lower() == "user":
                txt = (msg.get("text") or "").strip()
                if txt:
                    last_user = txt
                    break
        parts = [subject, first_action, last_user]
        return "\n".join(part for part in parts if part).strip()

    def _maybe_use_kb(self, ticket_ctx: Dict[str, Any], conv: Dict[str, Any], intent: Optional[str]) -> Optional[str]:
        query = self._build_kb_query_text(ticket_ctx, conv)
        if not query:
            return None
        try:
            priors = get_priors(intent=intent) if intent else None
            kb_hit = kb_try_answer(query, priors=priors)
            if kb_hit and kb_hit.get("sources"):
                return kb_hit["reply"]
        except Exception as e:
            logger.warning(f"[BOT] fallback KB falhou: {e}")
        return None

    async def _triage_with_hint(self, conv: dict, ticket_ctx: dict, extra_hint: Optional[str]):
        """Chama o agente IA com histórico (+ dica genérica quando necessário)."""
        hist = list(conv.get("hist", []))
        if extra_hint:
            hist.append({"role": "user", "text": extra_hint})

        try:
            out = triage_next(hist, ticket_ctx)
        except Exception as e:
            logger.exception(f"[BOT] triage_next falhou: {e}")
            out = {
                "action": "ask",
                "message": "Certo! Em qual tela/opção você está agora? Posso te guiar o próximo passo.",
                "checklist": [],
                "confidence": 0.3,
            }

        reply = out.get("message") or "Certo. Em qual tela/opção você está agora?"
        checklist = out.get("checklist") or []
        if checklist:
            reply += "\n\n" + "\n".join(f"- {p}" for p in checklist)

        # 🔎 guarda doc e intenção selecionados pelo agente para feedback posterior
        conv["best_doc_path"] = out.get("best_doc_path")
        conv["best_intent"] = out.get("intent")

        return reply, out

    def _normalize_hist(self, conv: Dict[str, Any]) -> None:
        conv.setdefault("hist", [])
        norm_hist: List[Dict[str, str]] = []
        for m in conv["hist"]:
            txt = m.get("text")
            if not isinstance(txt, str):
                txt = m.get("content") if isinstance(m.get("content"), str) else ""
            norm_hist.append({"role": (m.get("role") or "user"), "text": (txt or "")})
        conv["hist"] = norm_hist

    async def _publish_summary_and_optionally_close(self, turn_context: TurnContext, conv: dict, close: bool):
        from app.summarizer import summarize_conversation  # gera resumo curto
        from app.movidesk_client import add_public_note, close_ticket  # ação pública e fechamento

        ticket_id = int(conv.get("ticket") or 0)
        transcript = "\n".join(f"{m['role']}: {m['text']}" for m in conv.get("hist", []))
        resumo = summarize_conversation(transcript)

        # ação pública no Movidesk
        try:
            add_public_note(ticket_id, resumo)
        except Exception:
            logger.warning(f"[BOT] falha ao adicionar nota pública no ticket {ticket_id}")

        if close:
            try:
                close_ticket(ticket_id)
                await turn_context.send_activity(
                    "✅ Perfeito! Registrei o resumo no chamado e **encerrei como resolvido**. "
                    "Se precisar, é só reabrir por aqui."
                )
            except Exception:
                await turn_context.send_activity(
                    "✅ Registrei o resumo no chamado. **Tentei encerrar** como resolvido; "
                    "se algo falhar o analista verifica."
                )
        else:
            await turn_context.send_activity(
                "👍 Registrei o resumo no chamado. Um analista **seguirá com o atendimento**."
            )

    def _reset_conversation(self, conv: dict) -> None:
        """Encerra o atendimento atual e limpa estado para não vazar para o próximo ticket."""
        conv.update(
            {
                "flow": None,
                "ticket": None,
                "subject": "",
                "ctx": "",
                "awaiting_ok": False,
                "best_doc_path": None,  # limpar doc selecionado
                "best_intent": None,    # limpar intenção selecionada
            }
        )
        conv["hist"] = []
        conv["agent_msgs"] = 0
        conv["ticket_list_cache"] = []

    def _extract_user_identity(self, turn_context: TurnContext) -> tuple[Optional[str], Optional[str]]:
        user = getattr(turn_context.activity, "from_property", None)
        email = None
        teams_id = None
        if user:
            teams_id = getattr(user, "aad_object_id", None) or getattr(user, "id", None)
            email = getattr(user, "email", None)
            extras = getattr(user, "additional_properties", None) or {}
            email = email or extras.get("email") or extras.get("userPrincipalName")
        channel_data = getattr(turn_context.activity, "channel_data", None) or {}
        if not email:
            user_data = channel_data.get("user") if isinstance(channel_data, dict) else None
            if isinstance(user_data, dict):
                email = user_data.get("email") or user_data.get("userPrincipalName")
        if not teams_id and isinstance(channel_data, dict):
            teams_id = channel_data.get("aadObjectId") or channel_data.get("teamsUserId")
        return email, teams_id

    def _sync_user_context(self, conv: Dict[str, Any]) -> None:
        email = conv.get("user_email")
        teams_user_id = conv.get("teams_user_id")
        ctx = None
        if email:
            ctx = get_user_context(email)
        elif teams_user_id:
            ctx = get_user_context_by_teams_id(teams_user_id)
            if ctx and ctx.get("user_email"):
                conv["user_email"] = ctx["user_email"]
                email = conv["user_email"]
        if ctx and ctx.get("teams_user_id") and not conv.get("teams_user_id"):
            conv["teams_user_id"] = ctx["teams_user_id"]
        if ctx and ctx.get("current_ticket_id") and not conv.get("ticket"):
            ticket_id = ctx["current_ticket_id"]
            conv["ticket"] = ticket_id
            self._hydrate_ticket_from_db(conv, ticket_id)
        if conv.get("user_email") and conv.get("teams_user_id"):
            set_user_current_ticket(conv["user_email"], conv.get("ticket"), teams_user_id=conv["teams_user_id"])

    def _hydrate_ticket_from_db(self, conv: Dict[str, Any], ticket_id: int) -> None:
        rec = get_ticket_rec(ticket_id)
        if rec:
            conv["subject"] = rec.get("subject") or conv.get("subject") or ""
            conv["ctx"] = conv.get("ctx") or (rec.get("n1_reason") or "")

    async def _activate_ticket(
        self,
        turn_context: TurnContext,
        conv: Dict[str, Any],
        ticket_id: int,
        user_email: Optional[str],
        teams_user_id: Optional[str],
        send_opening: bool = True,
    ):
        try:
            bundle = get_ticket_text_bundle(ticket_id)
        except Exception:
            bundle = {"subject": "", "first_action_text": "", "first_action_html": ""}
        conv.update(
            {
                "flow": "triage",
                "ticket": ticket_id,
                "subject": bundle.get("subject") or "",
                "ctx": (bundle.get("first_action_text") or bundle.get("first_action_html") or "").strip(),
                "hist": [],
                "agent_msgs": 0,
                "awaiting_ok": False,
                "best_doc_path": None,
                "best_intent": None,
            }
        )
        if user_email:
            set_user_current_ticket(user_email, ticket_id, teams_user_id=teams_user_id)
        if not send_opening:
            subject = conv.get("subject") or f"Ticket #{ticket_id}"
            conv["hist"].append({"role": "assistant", "text": f"Ok! Continuamos no ticket #{ticket_id}: **{subject}**."})
            await turn_context.send_activity(
                f"Ok! Continuamos no ticket #{ticket_id}: **{subject}**.\nMe conte o que está acontecendo para eu ajudar."
            )
            await self._save(turn_context, conv)
            return

        user_full_name = (turn_context.activity.from_property.name if hasattr(turn_context.activity.from_property, 'name') and turn_context.activity.from_property.name else "Usuário")
        subject = conv.get('subject','(sem assunto)')
        prompt = build_initial_prompt(user_full_name, ticket_id, subject)
        opening = f"Ótimo! Vamos começar pelo #{ticket_id}: **{subject}**.\nVou te guiar. Caso apareça algum erro/tela diferente, me diga o que aparece."
        try:
            from app.ai.triage_agent import ia_generate_message  # lazy
            generated = ia_generate_message(prompt)
            if generated.strip():
                opening = generated.strip()
        except Exception:
            logger.warning("[BOT] não foi possível gerar saudação via IA (usa fallback).")
        conv["hist"].append({"role": "assistant", "text": opening})
        conv["agent_msgs"] += 1
        await turn_context.send_activity(opening)

        ticket_ctx = {
            "id": ticket_id,
            "subject": conv.get("subject") or "",
            "first_action_text": conv.get("ctx") or "",
        }
        reply, out = await self._triage_with_hint(conv, ticket_ctx, extra_hint=None)
        action = (out.get("action") or "").lower()
        confidence = float(out.get("confidence") or 0) if isinstance(out.get("confidence"), (int, float, str)) else 0.0
        if action == "escalate":
            reply = (
                (out.get("message") or "Este atendimento precisa de um técnico por envolver permissões administrativas.").strip()
                + "\n\n"
                "👍 Vou **encaminhar para um técnico** e **registrar o resumo da nossa conversa no chamado**. "
                "Você será notificado quando houver atualização."
            )
            conv["awaiting_ok"] = False
            conv["hist"].append({"role": "assistant", "text": reply})
            conv["agent_msgs"] += 1
            await turn_context.send_activity(reply)
            try:
                if conv.get("best_doc_path"):
                    record_feedback(
                        doc_path=conv.get("best_doc_path") or "",
                        success=False,
                        intent=conv.get("best_intent"),
                        ticket_id=str(conv.get("ticket")),
                    )
            except Exception as e:
                logger.warning(f"[BOT] falha ao registrar feedback (escalate): {e}")
            await self._publish_summary_and_optionally_close(turn_context, conv, close=False)
            self._reset_conversation(conv)
            await self._save(turn_context, conv)
            return

        kb_reply = None
        if action in ("answer", "resolve") and confidence >= 0.45:
            kb_reply = self._maybe_use_kb(ticket_ctx, conv, out.get("intent"))

        has_steps = bool(out.get("checklist")) or action in ("answer", "resolve")
        if kb_reply:
            reply = f"{reply}\n\n{kb_reply}\n\n**Funcionou?** Responda *Sim* ou *Não*."
            conv["awaiting_ok"] = True
        elif has_steps:
            reply = reply.rstrip() + "\n\n**Funcionou?** Responda *Sim* ou *Não*."
            conv["awaiting_ok"] = True

        conv["hist"].append({"role": "assistant", "text": reply})
        conv["agent_msgs"] += 1
        await turn_context.send_activity(reply)
        await self._save(turn_context, conv)

    async def _send_status(self, turn_context: TurnContext, conv: Dict[str, Any]) -> None:
        ticket_id = conv.get("ticket")
        if not ticket_id:
            await turn_context.send_activity(
                "Você não selecionou nenhum ticket. Envie `listar` para ver os chamados em andamento e depois `continuar 1` para escolher um."
            )
            return
        rec = get_ticket_rec(ticket_id)
        if not rec:
            await turn_context.send_activity("Não encontrei dados locais deste ticket. Tente `iniciar <id>` novamente.")
            return
        await turn_context.send_activity(build_status_message(rec))

    # ---------------- eventos ----------------
    async def on_members_added_activity(self, members_added: List[ChannelAccount], turn_context: TurnContext):
        for member in members_added:
            if member.id != turn_context.activity.recipient.id:
                await turn_context.send_activity("✅ Online! Use `iniciar <ticket>` para começar.")

    # ---------------- mensagens ----------------
    async def on_message_activity(self, turn_context: TurnContext):
        text_raw: str = (turn_context.activity.text or "").strip()
        text = text_raw.lower()

        user_email, teams_user_id = self._extract_user_identity(turn_context)

        conv: Dict[str, Any] = await self.conv_accessor.get(turn_context) or {}  # type: ignore
        conv.setdefault("flow", None)
        conv.setdefault("ticket", None)
        conv.setdefault("subject", "")
        conv.setdefault("ctx", "")
        conv.setdefault("agent_msgs", 0)
        conv.setdefault("awaiting_ok", False)
        conv.setdefault("best_doc_path", None)
        conv.setdefault("best_intent", None)
        conv.setdefault("ticket_list_cache", [])
        if user_email:
            conv["user_email"] = user_email.lower()
        if teams_user_id:
            conv["teams_user_id"] = teams_user_id
        self._normalize_hist(conv)
        self._sync_user_context(conv)

        # -------- comandos ----------
        if text in ("listar", "listar tickets"):
            if not conv.get("user_email"):
                await turn_context.send_activity(
                    "Não consegui identificar seu e-mail para listar os tickets. Tente novamente em instantes."
                )
            else:
                tickets = list_tickets_for_requester(conv["user_email"], limit=5)
                conv["ticket_list_cache"] = tickets
                await turn_context.send_activity(format_ticket_listing(tickets))
            await self._save(turn_context, conv)
            return

        m_continue = re.match(r"^(?:continuar|ticket)\s+(\d+)$", text)
        if m_continue:
            choice = m_continue.group(1)
            tickets = conv.get("ticket_list_cache") or []
            ticket = resolve_ticket_choice(choice, tickets)
            if not ticket and choice.isdigit():
                ticket = {"ticket_id": int(choice), "subject": ""}
            if ticket and ticket.get("ticket_id"):
                await self._activate_ticket(
                    turn_context,
                    conv,
                    int(ticket["ticket_id"]),
                    conv.get("user_email"),
                    conv.get("teams_user_id"),
                    send_opening=False,
                )
            else:
                await turn_context.send_activity("Não consegui identificar esse ticket. Use `listar` e depois `continuar 1`.")
            return

        if text == "status":
            await self._send_status(turn_context, conv)
            await self._save(turn_context, conv)
            return

        # Aceita: "iniciar 12345", "12345" sozinho, ou "sim" quando já soubermos o ticket
        m = re.match(r"^iniciar\s+(\d+)$", text)
        m_num_only = re.match(r"^(\d+)$", text)

        if m or m_num_only:
            ticket_id = int(m.group(1) if m else m_num_only.group(1))  # type: ignore
            await self._activate_ticket(
                turn_context,
                conv,
                ticket_id,
                conv.get("user_email"),
                conv.get("teams_user_id"),
                send_opening=True,
            )
            return

        # “Sim”/“Não” sem ticket ativo → pedir número do chamado
        if conv.get("ticket") is None and (self._user_says_yes(text_raw) or self._user_says_no(text_raw)):
            await turn_context.send_activity(
                "Você não está com nenhum ticket selecionado. Envie `listar` para ver os chamados e `continuar 1` para escolher um antes de responder `Sim` ou `Não`."
            )
            await self._save(turn_context, conv)
            return

        # -------- conversa em andamento ----------
        if conv.get("ticket"):
            current_ticket_id = conv.get("ticket")
            user_email_ctx = conv.get("user_email")
            teams_id_ctx = conv.get("teams_user_id")
            # confirmação primeiro
            if conv.get("awaiting_ok"):
                if self._user_says_yes(text_raw):
                    # ✅ feedback positivo antes de encerrar
                    try:
                        if conv.get("best_doc_path"):
                            record_feedback(
                                doc_path=conv.get("best_doc_path") or "",
                                success=True,
                                intent=conv.get("best_intent"),
                                ticket_id=str(conv.get("ticket")),
                            )
                    except Exception as e:
                        logger.warning(f"[BOT] falha ao registrar feedback positivo: {e}")

                    conv["awaiting_ok"] = False
                    conv["hist"].append({"role": "user", "text": text_raw})
                    await self._publish_summary_and_optionally_close(turn_context, conv, close=True)
                    if user_email_ctx:
                        set_user_current_ticket(user_email_ctx, None, teams_user_id=teams_id_ctx)
                    self._reset_conversation(conv)
                    await self._save(turn_context, conv)
                    return

                if self._user_says_no(text_raw):
                    # ❌ feedback negativo (não resolveu)
                    try:
                        if conv.get("best_doc_path"):
                            record_feedback(
                                doc_path=conv.get("best_doc_path") or "",
                                success=False,
                                intent=conv.get("best_intent"),
                                ticket_id=str(conv.get("ticket")),
                            )
                    except Exception as e:
                        logger.warning(f"[BOT] falha ao registrar feedback negativo: {e}")

                    conv["awaiting_ok"] = False
                    conv["hist"].append({"role": "user", "text": text_raw})
                    await self._publish_summary_and_optionally_close(turn_context, conv, close=False)
                    if user_email_ctx:
                        set_user_current_ticket(user_email_ctx, None, teams_user_id=teams_id_ctx)
                    self._reset_conversation(conv)
                    await self._save(turn_context, conv)
                    return
                # qualquer outro texto segue fluxo, mas mantém awaiting_ok=True

            ticket_ctx = {
                "id": current_ticket_id,
                "subject": conv.get("subject") or "",
                "first_action_text": conv.get("ctx") or "",
            }

            # registra fala do usuário
            conv["hist"].append({"role": "user", "text": text_raw})

            # se travou, dá uma dica para o agente tentar rota alternativa
            hint = None
            if self._is_stuck(text_raw):
                hint = (
                    "O usuário relatou que não encontrou a opção/caminho ou que não deu certo. "
                    "Forneça um caminho alternativo SE existir OU faça UMA pergunta de desambiguação muito específica. "
                    "Não repita passos exatamente iguais. Use a KB para embasar a alternativa."
                )

            reply, out = await self._triage_with_hint(conv, ticket_ctx, hint)

            # ESCALONAR?
            action = (out.get("action") or "").lower()
            confidence = float(out.get("confidence") or 0) if isinstance(out.get("confidence"), (int, float, str)) else 0.0
            if action == "escalate":
                reply = (
                    (out.get("message") or "Este atendimento precisa de um técnico por envolver permissões administrativas.").strip()
                    + "\n\n"
                    "👍 Vou **encaminhar para um técnico** e **registrar o resumo da nossa conversa no chamado**. "
                    "Você será notificado quando houver atualização."
                )
                conv["awaiting_ok"] = False
                conv["hist"].append({"role": "assistant", "text": reply})
                conv["agent_msgs"] += 1
                await turn_context.send_activity(reply)
                try:
                    if conv.get("best_doc_path"):
                        record_feedback(
                            doc_path=conv.get("best_doc_path") or "",
                            success=False,
                            intent=conv.get("best_intent"),
                            ticket_id=str(conv.get("ticket")),
                        )
                except Exception as e:
                    logger.warning(f"[BOT] falha ao registrar feedback (escalate): {e}")
                await self._publish_summary_and_optionally_close(turn_context, conv, close=False)
                if user_email_ctx:
                    set_user_current_ticket(user_email_ctx, None, teams_user_id=teams_id_ctx)
                self._reset_conversation(conv)
                await self._save(turn_context, conv)
                return

            # proteção contra repetição
            last_assistant = next((m["text"] for m in reversed(conv["hist"]) if m.get("role") == "assistant"), "")
            if last_assistant.strip() == reply.strip():
                alt_hint = (
                    "A resposta anterior saiu igual. Agora NÃO repita. "
                    "Dê uma alternativa concreta (ex.: caminho diferente, tecla/menu alternativo) "
                    "OU faça uma pergunta de desambiguação específica e única. Curto e objetivo."
                )
                reply, out = await self._triage_with_hint(conv, ticket_ctx, alt_hint)
                action = (out.get("action") or "").lower()
                confidence = float(out.get("confidence") or 0) if isinstance(out.get("confidence"), (int, float, str)) else 0.0

            # limite de 25 mensagens do agente
            if conv["agent_msgs"] >= 25:
                await turn_context.send_activity(
                    "Chegamos ao limite de tentativas automáticas. Vou encaminhar para um técnico e registrar o resumo no chamado."
                )
                await self._publish_summary_and_optionally_close(turn_context, conv, close=False)
                if user_email_ctx:
                    set_user_current_ticket(user_email_ctx, None, teams_user_id=teams_id_ctx)
                self._reset_conversation(conv)
                await self._save(turn_context, conv)
                return

            kb_reply = None
            if action in ("answer", "resolve") and confidence >= 0.45:
                kb_reply = self._maybe_use_kb(ticket_ctx, conv, out.get("intent"))

            # confirmação quando houver passo-a-passo
            has_steps = bool(out.get("checklist")) or action in ("answer", "resolve")
            if kb_reply:
                reply = f"{reply}\n\n{kb_reply}\n\n**Funcionou?** Responda *Sim* ou *Não*."
                conv["awaiting_ok"] = True
            elif has_steps:
                reply = reply.rstrip() + "\n\n**Funcionou?** Responda *Sim* ou *Não*."
                conv["awaiting_ok"] = True

            conv["hist"].append({"role": "assistant", "text": reply})
            conv["agent_msgs"] += 1
            await turn_context.send_activity(reply)
            await self._save(turn_context, conv)
            return

        # sem ticket ainda
        await turn_context.send_activity("Para começar, me diga o número do chamado (ex.: `iniciar 12345` ou só o número).")
        await self._save(turn_context, conv)


# Resumo: este bot agora suporta múltiplos tickets por usuário (`listar`, `continuar <n>`, `status`)
# e se integra com follow-ups proativos para ajustar automaticamente o contexto ativo antes de
# processar respostas como "Sim" ou "Não".
