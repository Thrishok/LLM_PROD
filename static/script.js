const messagesEl = document.getElementById("messages");
const form = document.getElementById("chat-form");
const input = document.getElementById("input");
const sendBtn = document.getElementById("send-btn");
const resetBtn = document.getElementById("reset-btn");

// Persist the session id so refreshing the page keeps the conversation.
let sessionId = localStorage.getItem("session_id") || null;

function addMessage(text, role) {
  const wrap = document.createElement("div");
  wrap.className = `message ${role}`;
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;
  wrap.appendChild(bubble);
  messagesEl.appendChild(wrap);
  scrollToBottom();
  return wrap;
}

function showTyping() {
  const wrap = document.createElement("div");
  wrap.className = "message assistant typing";
  wrap.innerHTML = `<div class="bubble"><span></span><span></span><span></span></div>`;
  messagesEl.appendChild(wrap);
  scrollToBottom();
  return wrap;
}

function scrollToBottom() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function autoGrow() {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 160) + "px";
}

async function sendMessage(text) {
  addMessage(text, "user");
  const typing = showTyping();
  sendBtn.disabled = true;

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, session_id: sessionId }),
    });

    typing.remove();

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      addMessage(err.detail || `Request failed (${res.status}).`, "error");
      return;
    }

    const data = await res.json();
    sessionId = data.session_id;
    localStorage.setItem("session_id", sessionId);
    addMessage(data.response, "assistant");
  } catch (e) {
    typing.remove();
    addMessage("Network error — is the server running?", "error");
  } finally {
    sendBtn.disabled = false;
    input.focus();
  }
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  autoGrow();
  sendMessage(text);
});

// Enter sends, Shift+Enter inserts a newline.
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    form.requestSubmit();
  }
});

input.addEventListener("input", autoGrow);

resetBtn.addEventListener("click", async () => {
  if (sessionId) {
    await fetch(`/api/session/${sessionId}`, { method: "DELETE" }).catch(() => {});
  }
  sessionId = null;
  localStorage.removeItem("session_id");
  messagesEl.innerHTML = "";
  addMessage("Hi! I'm a LangGraph assistant with web search. Ask me anything.", "assistant");
  input.focus();
});

input.focus();
