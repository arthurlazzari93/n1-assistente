import React, { useEffect, useRef, useState } from "react";

const SEEDS = [
  {
    id: "onedrive_sync",
    title: "OneDrive não sincroniza",
    subject: "OneDrive preso sincronizando biblioteca Projetos",
    description:
      "Usuário relata que a pasta Documentos fica com ícone amarelo e mensagem 'Processando alterações' há horas. Precisa liberar espaço para enviar relatório."
  },
  {
    id: "sharepoint_perms",
    title: "Sem acesso SharePoint",
    subject: "Erro de permissão ao abrir site financeiro",
    description:
      "Ao abrir o site https://contoso.sharepoint.com/sites/Financeiro o usuário recebe 'Acesso negado' mesmo estando no grupo da área."
  },
  {
    id: "printer_queue",
    title: "Fila de impressora travada",
    subject: "Fila da impressora HP-LaserFinance parada",
    description:
      "Impressora do Financeiro parou, há vários documentos 'Em impressão'. Reiniciar spooler não resolveu. Usuário precisa limpar fila."
  },
  {
    id: "erp_access",
    title: "ERP nega acesso",
    subject: "Usuário sem perfil no ERP TOTVS",
    description:
      "Usuário não consegue acessar menu financeiro no ERP TOTVS, mensagem: usuário sem permissão. Precisa liberar acesso urgente."
  },
  {
    id: "email_delivery",
    title: "Email externo não chega",
    subject: "Clientes não conseguem enviar email para suporte",
    description:
      "Clientes reportam que os e-mails enviados para suporte@empresa.com retornam com erro 550. Internamente funciona."
  },
  {
    id: "other_generic",
    title: "Cenário genérico",
    subject: "Duvida sobre política interna",
    description: "Usuário pergunta quais são os horários de funcionamento da TI. Não há ticket claro."
  }
];

const MODE_DESCRIPTIONS = {
  ticket:
    "Simule um chamado recebido no Movidesk e veja como o agente usa a triagem e a KB.",
  chat: "Converse livremente com o agente, como se chamasse a TI no Teams sem ticket aberto."
};

const CHAT_MODE_DEFAULT_SUBJECT = "[CHAT DIRETO] Sandbox Assistente N1";
const CHAT_MODE_DEFAULT_DESCRIPTION =
  "Conversa direta iniciada no sandbox (sem ticket Movidesk).";
const seedToMessage = (seed) =>
  `${seed.subject}. ${seed.description}`;

const TICKET_CONTEXT_ERROR =
  "Defina pelo menos um assunto ou uma descrição inicial do chamado antes de conversar com o agente.";

const createEmptyMetrics = () => ({
  action: null,
  intent: null,
  confidence: null
});

function ChatPage({ onResetSession }) {
  const [history, setHistory] = useState([]);
  const [message, setMessage] = useState("");
  const [subject, setSubject] = useState("");
  const [description, setDescription] = useState("");
  const [sessionStarted, setSessionStarted] = useState(false);
  const [metrics, setMetrics] = useState(() => createEmptyMetrics());
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [lastRequest, setLastRequest] = useState(null);
  const [lastResponse, setLastResponse] = useState(null);
  const [showLogs, setShowLogs] = useState(false);
  const [activeLogTab, setActiveLogTab] = useState("request");
  const [selectedSeed, setSelectedSeed] = useState(null);
  const [metricsSummary, setMetricsSummary] = useState(null);
  const [metricsLoading, setMetricsLoading] = useState(false);
  const [metricsError, setMetricsError] = useState("");
  const [mode, setMode] = useState("ticket"); // ticket | chat

  const chatRef = useRef(null);
  const isTicketMode = mode === "ticket";
  const isChatMode = mode === "chat";

  useEffect(() => {
    if (chatRef.current) {
      chatRef.current.scrollTop = chatRef.current.scrollHeight;
    }
  }, [history, loading]);

  const callAgent = async (historyPayload) => {
    const subjTrimmed = subject.trim();
    const descTrimmed = description.trim();
    let effectiveSubject = subjTrimmed;
    let effectiveDescription = descTrimmed;
    if (mode === "chat") {
      if (!effectiveSubject) {
        effectiveSubject = CHAT_MODE_DEFAULT_SUBJECT;
      }
      if (!effectiveDescription) {
        const lastUserMessage = [...historyPayload]
          .reverse()
          .find((msg) => msg.role === "user" && msg.text);
        effectiveDescription =
          lastUserMessage?.text?.trim() || CHAT_MODE_DEFAULT_DESCRIPTION;
      }
    }
    const payload = {
      mode,
      ticket: {
        subject: effectiveSubject,
        description: effectiveDescription
      },
      history: historyPayload
    };
    setLastRequest(payload);
    const res = await fetch("/debug/chat/triage", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });

    if (!res.ok) {
      throw new Error(`Falha ao chamar /debug/chat/triage (${res.status})`);
    }

    const data = await res.json();
    setLastResponse(data);
    return data;
  };

  const handleSend = async () => {
    if (loading) return;
    setError("");
    const trimmed = message.trim();
    if (!trimmed) return;

    let historyBase = history;
    if (!sessionStarted) {
      if (mode === "chat") {
        historyBase = [];
        setMetrics(createEmptyMetrics());
        setSessionStarted(true);
      } else {
        setError(TICKET_CONTEXT_ERROR);
        return;
      }
    }

    if (mode === "ticket") {
      const subj = subject.trim();
      const desc = description.trim();
      if (!subj && !desc) {
        setError(TICKET_CONTEXT_ERROR);
        return;
      }
    }

    const nextHistory = [...historyBase, { role: "user", text: trimmed }];
    setHistory(nextHistory);
    setMessage("");
    setLoading(true);

    try {
      const data = await callAgent(nextHistory);
      const reply = data.reply || "(sem resposta)";

      setHistory((prev) => [...prev, { role: "assistant", text: reply }]);
      setMetrics({
        action: data.action ?? null,
        intent: data.intent ?? null,
        confidence:
          typeof data.confidence === "number" ? data.confidence : null
      });
    } catch (err) {
      console.error(err);
      setError(
        "Erro ao conversar com o agente. Verifique se o backend FastAPI está rodando em http://localhost:8000."
      );
    } finally {
      setLoading(false);
    }
  };

  const handleStartAgent = async () => {
    if (loading) return;
    setError("");

    if (mode === "chat") {
      if (!sessionStarted) {
        setMetrics(createEmptyMetrics());
        setSessionStarted(true);
      }
      return;
    }

    if (sessionStarted) return;

    const subj = subject.trim();
    const desc = description.trim();
    if (!subj && !desc) {
      setError(TICKET_CONTEXT_ERROR);
      return;
    }

    setLoading(true);
    try {
      const data = await callAgent([]);
      const reply = data.reply || "(sem resposta)";
      setHistory([{ role: "assistant", text: reply }]);
      setMetrics({
        action: data.action ?? null,
        intent: data.intent ?? null,
        confidence:
          typeof data.confidence === "number" ? data.confidence : null
      });
      setSessionStarted(true);
    } catch (err) {
      console.error(err);
      setError(
        "Erro ao iniciar o agente. Verifique se o backend FastAPI está rodando em http://localhost:8000."
      );
    } finally {
      setLoading(false);
    }
  };

  const resetSessionState = () => {
    setHistory([]);
    setMetrics(createEmptyMetrics());
    setError("");
    setMessage("");
    setSessionStarted(false);
    setLastRequest(null);
    setLastResponse(null);
    setSelectedSeed(null);
    setMetricsSummary(null);
  };

  const handleReset = () => {
    resetSessionState();
    if (typeof onResetSession === "function") {
      onResetSession();
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleSeedSelect = (seed) => {
    if (mode === "chat") {
      setMessage(seedToMessage(seed));
    } else {
      setSubject(seed.subject);
      setDescription(seed.description);
    }
    setSelectedSeed(seed.id);
    setSessionStarted(false);
    setHistory([]);
    setMetrics(createEmptyMetrics());
  };

  const handleModeChange = (nextMode) => {
    if (nextMode === mode) return;
    resetSessionState();
    setSubject("");
    setDescription("");
    setMessage("");
    setError("");
    setMode(nextMode);
  };

  const handleCopyJson = (type) => {
    const payload = type === "request" ? lastRequest : lastResponse;
    if (!payload) return;
    navigator.clipboard.writeText(JSON.stringify(payload, null, 2));
  };

  const fetchMetrics = async () => {
    setMetricsLoading(true);
    setMetricsError("");
    try {
      const res = await fetch("/debug/metrics");
      if (!res.ok) {
        throw new Error(`Falha ao obter métricas (${res.status})`);
      }
      const data = await res.json();
      setMetricsSummary(data);
    } catch (err) {
      console.error(err);
      setMetricsError("Não foi possível carregar as métricas.");
    } finally {
      setMetricsLoading(false);
    }
  };

  const renderLogContent = () => {
    const target = activeLogTab === "request" ? lastRequest : lastResponse;
    if (!target) {
      return <div className="logs-empty">Sem dados ainda.</div>;
    }
    return (
      <pre className="logs-json">
        {JSON.stringify(target, null, 2)}
      </pre>
    );
  };

  const ingestWindow = metricsSummary?.ingest?.window;
  const followSummary = metricsSummary?.followups;

  const renderMetrics = () => {
    const { action, intent, confidence } = metrics;
    if (!action && !intent && confidence == null) {
      return <span className="pill-small">Aguardando interação...</span>;
    }

    const items = [];
    if (intent) {
      items.push(
        <span key="intent" className="metric">
          <strong>Intent</strong> {intent}
        </span>
      );
    }
    if (action) {
      items.push(
        <span key="action" className="metric">
          <strong>Ação</strong> {action}
        </span>
      );
    }
    if (confidence != null) {
      items.push(
        <span key="confidence" className="metric">
          <strong>Confiança</strong> {Math.round(confidence * 100)}%
        </span>
      );
    }
    return items;
  };

  const disableContext = isChatMode;
  const messageControlsDisabled = loading || (isTicketMode && !sessionStarted);
  const inputPlaceholder = isChatMode
    ? "Digite sua dúvida e eu respondo por aqui..."
    : sessionStarted
    ? "Descreva o próximo passo ou responda ao agente..."
    : 'Clique em "Iniciar agente" para começar';
  const sessionStatus = sessionStarted
    ? {
        label: isTicketMode ? "Ticket em atendimento" : "Chat em andamento",
        tone: "active",
        hint: isTicketMode
          ? "Responda ao assistente para concluir a triagem."
          : "Você pode mandar novas mensagens quando quiser."
      }
    : isTicketMode
    ? {
        label: "Aguardando contexto do chamado",
        tone: "idle",
        hint: 'Informe assunto ou descrição e clique em "Iniciar agente".'
      }
    : {
        label: "Pronto para chat direto",
        tone: "ready",
        hint: "Digite sua dúvida e aperte Enter para iniciar."
      };
  const bottomHint = sessionStatus.hint;

  return (
    <div className={`app-shell ${showLogs ? "with-logs" : ""}`}>
      <header className="app-header">
        <div className="avatar">N1</div>
        <div className="app-title">
          <h1>Assistente N1 – Sandbox</h1>
          <span>Simulador de atendimento de chamado (React + Vite)</span>
        </div>
        <div className="badge">
          <div className="badge-dot" />
          Frontend em teste
        </div>
      </header>

      <main className={`app-main ${showLogs ? "show-logs" : ""}`}>
        <section className="chat-panel">
          <div className="seed-bar">
            <div className="seed-label">Seeds rápidos:</div>
            <div className="seed-buttons">
              {SEEDS.map((seed) => (
                <button
                  key={seed.id}
                  className={`seed-btn ${selectedSeed === seed.id ? "active" : ""}`}
                  onClick={() => handleSeedSelect(seed)}
                >
                  {seed.title}
                </button>
              ))}
            </div>
          </div>
          <div className="session-toolbar">
            <div className="session-info">
              <span className="system-pill">Sessão de teste</span>
              <p>{MODE_DESCRIPTIONS[mode]}</p>
            </div>
            <div className="session-toolbar-actions">
              <div className={`session-status session-status--${sessionStatus.tone}`}>
                <span className="session-status__dot" />
                {sessionStatus.label}
              </div>
              <div
                className="mode-toggle"
                role="group"
                aria-label="Selecionar modo de simulação"
              >
                <button
                  className={`mode-toggle__btn ${isTicketMode ? "active" : ""}`}
                  onClick={() => handleModeChange("ticket")}
                >
                  Chamado Movidesk
                </button>
                <button
                  className={`mode-toggle__btn ${isChatMode ? "active" : ""}`}
                  onClick={() => handleModeChange("chat")}
                >
                  Chat direto com a TI
                </button>
              </div>
            </div>
          </div>
          <div ref={chatRef} className="chat-body">
            {history.map((msg, idx) => {
              if (msg.role === "system") return null;
              const role = msg.role === "assistant" ? "agent" : "user";
              return (
                <div key={idx} className={`chat-message ${role}`}>
                  <div className="chat-bubble">{msg.text}</div>
                </div>
              );
            })}

            {loading && (
              <div className="chat-message agent">
                <div className="chat-bubble">
                  <div className="typing">
                    <span className="typing-dot" />
                    <span className="typing-dot" />
                    <span className="typing-dot" />
                  </div>
                </div>
              </div>
            )}
          </div>

          <div className="chat-input">
            <div className="chat-input-row">
              <div className="input-box">
                <input
                  type="text"
                  disabled={messageControlsDisabled}
                  value={message}
                  onChange={(e) => setMessage(e.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder={inputPlaceholder}
                  autoComplete="off"
                />
              </div>
              <button
                className="btn-primary"
                onClick={handleSend}
                disabled={messageControlsDisabled}
              >
                Enviar
              </button>
            </div>
            <div className="chat-input-row chat-actions">
              <button
                className="btn-accent"
                onClick={handleStartAgent}
                disabled={loading || (isTicketMode && sessionStarted)}
              >
                Iniciar agente
              </button>
              <button className="btn-secondary" onClick={handleReset} disabled={loading}>
                Resetar conversa
              </button>
              <div className="input-placeholder hide-mobile">
                {bottomHint}
              </div>
            </div>
          </div>
        </section>

        <aside className="ctx-panel">
          <div className="ctx-header">
            <div>
              <strong>Contexto do chamado</strong>
              <div className="ctx-subtitle">
                {mode === "chat"
                  ? "Modo chat direto: campos desativados. A conversa começa pela mensagem abaixo."
                  : "Assunto e descrição inicial usados como ticket pelo agente."}
              </div>
            </div>
          </div>

          <div className="ctx-group">
            <label htmlFor="subject-input">Assunto</label>
            <input
              id="subject-input"
              type="text"
              disabled={disableContext}
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              placeholder="Ex: Erro ao configurar assinatura no Outlook"
            />
          </div>

          <div className="ctx-group">
            <label htmlFor="description-input">Descrição inicial do problema</label>
            <textarea
              id="description-input"
              disabled={disableContext}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Ex: Usuário relata que a assinatura não aparece ao criar novos e-mails..."
            />
          </div>

          <div className="ctx-footer">
            <span>
              {mode === "chat"
                ? "No modo chat direto não usamos assunto/descrição — basta iniciar a conversa."
                : "Estes campos simulam o texto do ticket no Movidesk."}
            </span>
          </div>
          <div className="metrics-panel">
            <div className="metrics-panel__header">
              <strong>Ferramentas de debug</strong>
              <div className="metrics-actions">
                <button className="btn-secondary" onClick={fetchMetrics} disabled={metricsLoading}>
                  {metricsLoading ? "Carregando..." : "Atualizar métricas"}
                </button>
                <label className="logs-toggle">
                  <input
                    type="checkbox"
                    checked={showLogs}
                    onChange={() => setShowLogs((prev) => !prev)}
                  />
                  Mostrar logs JSON
                </label>
              </div>
            </div>
            {metricsError && <div className="error-banner">{metricsError}</div>}
            {metricsSummary && (
              <ul className="metrics-panel__list">
                <li>
                  Eventos 24h: {ingestWindow?.total_events ?? "-"} | Erros:{" "}
                  {ingestWindow?.by_status?.error ?? 0}
                </li>
                <li>Follow-ups pendentes: {followSummary?.pending_total ?? 0}</li>
                <li>Próximo follow-up: {followSummary?.next_due || "—"}</li>
              </ul>
            )}
          </div>

          <div className="ctx-group">
            <label>Métricas da última resposta</label>
            <div className="metrics">{renderMetrics()}</div>
          </div>

          {error &&
            (isTicketMode || error !== TICKET_CONTEXT_ERROR) && (
              <div className="error-banner">{error}</div>
            )}
        </aside>
        {showLogs && (
          <aside className="logs-panel">
            <div className="logs-header">
              <strong>Logs JSON</strong>
              <div className="logs-tabs">
                <button
                  className={activeLogTab === "request" ? "active" : ""}
                  onClick={() => setActiveLogTab("request")}
                >
                  Request
                </button>
                <button
                  className={activeLogTab === "response" ? "active" : ""}
                  onClick={() => setActiveLogTab("response")}
                >
                  Response
                </button>
              </div>
              <button
                className="btn-secondary btn-copy"
                onClick={() => handleCopyJson(activeLogTab)}
                disabled={
                  (activeLogTab === "request" && !lastRequest) ||
                  (activeLogTab === "response" && !lastResponse)
                }
              >
                Copiar JSON
              </button>
            </div>
            {renderLogContent()}
          </aside>
        )}
      </main>
    </div>
  );
}

export default ChatPage;
