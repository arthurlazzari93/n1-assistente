import React, { useState } from "react";
import ChatPage from "./components/ChatPage.jsx";

function App() {
  const [key, setKey] = useState(0);

  const handleResetSession = () => {
    setKey((k) => k + 1);
  };

  return (
    <ChatPage key={key} onResetSession={handleResetSession} />
  );
}

export default App;

