import React, { useEffect, useRef, useState } from "react";

const initialBanner = {
  role: "system",
  text: "Converse livremente com o agente para avaliar o fluxo de triagem, uso da KB e parâmetros da IA."
};

function ChatPage({ onResetSession }) {
  const [history, setHistory] = useState([]);
  const [message, setMessage] = useState("");
  const [subject, setSubject] = useState("");
  const [description, setDescription] = useState("");
  const [sessionStarted, setSessionStarted] = useState(false);
  const [metrics, setMetrics] = useState({
    action: null,
    intent: null,
    confidence: null
  });
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const chatRef = useRef(null);

  useEffect(() => {
    if (chatRef.current) {
      chatRef.current.scrollTop = chatRef.current.scrollHeight;
    }
  }, [history, loading]);

  const callAgent = async (historyPayload) => {
    const res = await fetch("/debug/chat/triage", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ticket: {
          subject: subject.trim(),
          description: description.trim()
        },
        history: historyPayload
      })
    });

    if (!res.ok) {
      throw new Error(`Falha ao chamar /debug/chat/triage (${res.status})`);
    }

    return res.json();
  };

  const handleSend = async () => {
    if (loading) return;
    setError("");
    if (!sessionStarted) {
      setError('Clique em "Iniciar agente" para começar a conversa.');
      return;
    }

    const trimmed = message.trim();
    if (!trimmed) return;

    const subj = subject.trim();
    const desc = description.trim();

    if (!subj && !desc) {
      setError(
        "Defina pelo menos um assunto ou uma descrição inicial do chamado antes de conversar com o agente."
      );
      return;
    }

    const nextHistory = [...history, { role: "user", text: trimmed }];
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
    if (loading || sessionStarted) return;
    setError("");

    const subj = subject.trim();
    const desc = description.trim();
    if (!subj && !desc) {
      setError(
        "Defina pelo menos um assunto ou uma descrição inicial do chamado antes de iniciar o agente."
      );
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

  const handleReset = () => {
    setHistory([]);
    setMetrics({ action: null, intent: null, confidence: null });
    setError("");
    setMessage("");
    setSessionStarted(false);
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

  return (
    <div className="app-shell">
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

      <main className="app-main">
        <section className="chat-panel">
          <div ref={chatRef} className="chat-body">
            <div className="system-banner">
              <span className="system-pill">Sessão de teste</span>
              {initialBanner.text}
            </div>

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
                  disabled={!sessionStarted || loading}
                  value={message}
                  onChange={(e) => setMessage(e.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder={
                    sessionStarted
                      ? "Descreva o próximo passo ou responda ao agente..."
                      : 'Clique em "Iniciar agente" para começar'
                  }
                  autoComplete="off"
                />
              </div>
              <button
                className="btn-primary"
                onClick={handleSend}
                disabled={loading || !sessionStarted}
              >
                Enviar
              </button>
            </div>
            <div className="chat-input-row chat-actions">
              <button
                className="btn-accent"
                onClick={handleStartAgent}
                disabled={loading || sessionStarted}
              >
                Iniciar agente
              </button>
              <button className="btn-secondary" onClick={handleReset} disabled={loading}>
                Resetar conversa
              </button>
              <div className="input-placeholder hide-mobile">
                Dica: simule um chamado real e acompanhe como o agente usa a KB.
              </div>
            </div>
          </div>
        </section>

        <aside className="ctx-panel">
          <div className="ctx-header">
            <div>
              <strong>Contexto do chamado</strong>
              <div className="ctx-subtitle">
                Assunto e descrição inicial usados como ticket pelo agente.
              </div>
            </div>
          </div>

          <div className="ctx-group">
            <label htmlFor="subject-input">Assunto</label>
            <input
              id="subject-input"
              type="text"
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              placeholder="Ex: Erro ao configurar assinatura no Outlook"
            />
          </div>

          <div className="ctx-group">
            <label htmlFor="description-input">Descrição inicial do problema</label>
            <textarea
              id="description-input"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Ex: Usuário relata que a assinatura não aparece ao criar novos e-mails..."
            />
          </div>

          <div className="ctx-footer">
            <span>
              Estes campos simulam o texto do ticket no Movidesk.
            </span>
          </div>

          <div className="ctx-group">
            <label>Métricas da última resposta</label>
            <div className="metrics">{renderMetrics()}</div>
          </div>

          {error && <div className="error-banner">{error}</div>}
        </aside>
      </main>
    </div>
  );
}

export default ChatPage;
