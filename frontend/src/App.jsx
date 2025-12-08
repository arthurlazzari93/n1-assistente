import React, { useState } from "react";
import ChatPage from "./components/ChatPage.jsx";
import KbAdminPage from "./components/KbAdminPage.jsx";

const TABS = [
  { id: "chat", label: "Sandbox do agente" },
  { id: "kb", label: "Base de conhecimento" }
];

function App() {
  const [sessionKey, setSessionKey] = useState(0);
  const [activeTab, setActiveTab] = useState("chat");

  const handleResetSession = () => {
    setSessionKey((k) => k + 1);
  };

  return (
    <div className="app-root">
      <div className="global-tabs" role="tablist" aria-label="Selecionar módulo do sandbox">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            role="tab"
            aria-selected={activeTab === tab.id}
            className={`global-tabs__btn ${activeTab === tab.id ? "active" : ""}`}
            onClick={() => setActiveTab(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </div>
      {activeTab === "chat" ? (
        <ChatPage key={sessionKey} onResetSession={handleResetSession} />
      ) : (
        <KbAdminPage />
      )}
    </div>
  );
}

export default App;

