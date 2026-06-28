const messagesEl = document.getElementById("messages");
const form = document.getElementById("chat-form");
const input = document.getElementById("input");
const sendBtn = document.getElementById("send-btn");
const sidebar = document.getElementById("sidebar");
const convListEl = document.getElementById("conv-list");
const newChatBtn = document.getElementById("new-chat-btn");
const sidebarToggle = document.getElementById("sidebar-toggle");

const LOGO = "/logo_icon.png?v=3";

// Profile of the signed-in user (null when anonymous).
let currentUser = null;
let isAuthenticated = false;

// The conversation currently open (null = a fresh, unsaved chat).
let currentConversationId = null;

// Starter prompts shown on the welcome screen.
const SUGGESTIONS = [
  "What's the latest news in AI?",
  "Explain quantum computing in simple terms",
  "Give me a quick healthy breakfast idea",
  "Help me write a polite follow-up email",
];

// Build initials from a name ("Sourav Pal" -> "SP") or fall back to the email.
function initialsFor(name, email) {
  const n = (name || "").trim();
  if (n) {
    const parts = n.split(/\s+/);
    if (parts.length >= 2) return parts[0][0] + parts[1][0];
    return n.slice(0, 2);
  }
  return (email || "?").slice(0, 2);
}

// --------------------------------------------------------------------------- //
// Auth / profile
// --------------------------------------------------------------------------- //

// Fill an (img, initials) avatar pair from the user profile.
function fillAvatar(imgEl, initialsEl, user) {
  const label = user.name || user.email;
  if (user.picture) {
    imgEl.src = user.picture;
    imgEl.alt = label;
    imgEl.hidden = false;
    initialsEl.hidden = true;
  } else {
    initialsEl.textContent = initialsFor(user.name, user.email);
    initialsEl.hidden = false;
    imgEl.hidden = true;
  }
}

async function loadUser() {
  const badge = document.getElementById("user-badge");
  const headerUser = document.getElementById("header-user");
  const signoutLink = document.getElementById("signout-link");
  const signinLink = document.getElementById("signin-link");

  try {
    const res = await fetch("/api/me");

    if (!res.ok) {
      // Logged out: show the Sign in button, hide the avatar + sidebar.
      isAuthenticated = false;
      currentUser = null;
      signinLink.hidden = false;
      headerUser.hidden = true;
      badge.hidden = true;
      signoutLink.hidden = true;
      sidebar.hidden = true;
      sidebarToggle.hidden = true;
      return;
    }

    const user = await res.json();
    isAuthenticated = true;
    currentUser = user;
    const label = user.name || user.email;

    // Header avatar (replaces the Sign in button when logged in).
    fillAvatar(
      document.getElementById("header-avatar"),
      document.getElementById("header-initials"),
      user
    );
    headerUser.title = label;
    headerUser.hidden = false;
    signinLink.hidden = true;
    document.getElementById("menu-name").textContent = label;
    document.getElementById("menu-email").textContent = user.email;

    // Sidebar footer avatar + name.
    fillAvatar(
      document.getElementById("user-avatar"),
      document.getElementById("user-initials"),
      user
    );
    const nameEl = document.getElementById("user-name");
    if (nameEl) nameEl.textContent = label;
    badge.title = label;
    badge.hidden = false;
    signoutLink.hidden = false;

    sidebar.hidden = false;
    sidebarToggle.hidden = false;

    loadConversations();
  } catch (_) {
    signinLink.hidden = false;
  }
}

// --------------------------------------------------------------------------- //
// Conversation sidebar
// --------------------------------------------------------------------------- //

function trashIcon() {
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
    stroke-linecap="round" stroke-linejoin="round">
    <polyline points="3 6 5 6 21 6"></polyline>
    <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
  </svg>`;
}

function renderConversations(list) {
  convListEl.innerHTML = "";
  if (!list.length) {
    convListEl.innerHTML = `<div class="conv-empty">No conversations yet. Start chatting and they'll appear here.</div>`;
    return;
  }
  for (const c of list) {
    const item = document.createElement("div");
    item.className = "conv-item" + (c.id === currentConversationId ? " active" : "");
    item.dataset.id = c.id;
    item.innerHTML = `
      <span class="conv-title">${escapeHtml(c.title || "New chat")}</span>
      <button class="conv-del" type="button" title="Delete" data-del="${c.id}">${trashIcon()}</button>`;
    convListEl.appendChild(item);
  }
}

async function loadConversations() {
  if (!isAuthenticated) return;
  try {
    const res = await fetch("/api/conversations");
    if (!res.ok) return;
    renderConversations(await res.json());
  } catch (_) {
    /* ignore */
  }
}

async function openConversation(id) {
  try {
    const res = await fetch(`/api/conversations/${id}`);
    if (!res.ok) return;
    const conv = await res.json();
    currentConversationId = conv.id;
    messagesEl.innerHTML = "";
    for (const m of conv.messages) addMessage(m.content, m.role);
    markActive();
    if (window.matchMedia("(max-width: 768px)").matches) sidebar.hidden = true;
    input.focus();
  } catch (_) {
    /* ignore */
  }
}

function markActive() {
  for (const el of convListEl.querySelectorAll(".conv-item")) {
    el.classList.toggle("active", Number(el.dataset.id) === currentConversationId);
  }
}

async function deleteConversation(id) {
  try {
    await fetch(`/api/conversations/${id}`, { method: "DELETE" });
  } catch (_) {
    /* ignore */
  }
  if (id === currentConversationId) startNewChat();
  loadConversations();
}

function startNewChat() {
  currentConversationId = null;
  renderWelcome();
  markActive();
  input.focus();
}

// --------------------------------------------------------------------------- //
// Welcome screen
// --------------------------------------------------------------------------- //

function renderWelcome() {
  const chips = SUGGESTIONS.map(
    (s) => `<button class="chip" type="button">${escapeHtml(s)}</button>`
  ).join("");
  messagesEl.innerHTML = `
    <div id="welcome" class="welcome">
      <div class="welcome-logo"><img src="${LOGO}" alt="ZORA" /></div>
      <h2 class="welcome-title">How can I help you today?</h2>
      <p class="welcome-sub">Ask me anything — I can search the web for fresh, factual answers.</p>
      <div class="suggestions">${chips}</div>
    </div>`;
}

function hideWelcome() {
  const w = document.getElementById("welcome");
  if (w) w.remove();
}

// --------------------------------------------------------------------------- //
// Messages
// --------------------------------------------------------------------------- //

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

function buildAvatar(role) {
  const avatar = document.createElement("div");
  avatar.className = `msg-avatar ${role}`;
  if (role === "assistant") {
    avatar.innerHTML = `<img src="${LOGO}" alt="ZORA" />`;
  } else if (currentUser && currentUser.picture) {
    avatar.innerHTML = `<img src="${currentUser.picture}" alt="" />`;
  } else {
    avatar.textContent = currentUser
      ? initialsFor(currentUser.name, currentUser.email)
      : "Y";
  }
  return avatar;
}

function addMessage(text, role) {
  hideWelcome();

  const wrap = document.createElement("div");
  wrap.className = `message ${role}`;

  if (role === "error") {
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    bubble.textContent = text;
    wrap.appendChild(bubble);
    messagesEl.appendChild(wrap);
    scrollToBottom();
    return wrap;
  }

  const content = document.createElement("div");
  content.className = "msg-content";

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  if (role === "assistant") {
    bubble.innerHTML = marked.parse(text);
  } else {
    bubble.textContent = text;
  }
  content.appendChild(bubble);

  if (role === "assistant") {
    bubble.dataset.raw = text;
    const copy = document.createElement("button");
    copy.type = "button";
    copy.className = "copy-btn";
    copy.innerHTML = `
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
           stroke-linecap="round" stroke-linejoin="round">
        <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
        <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
      </svg>
      <span class="copy-label">Copy</span>`;
    content.appendChild(copy);
  }

  wrap.appendChild(buildAvatar(role));
  wrap.appendChild(content);
  messagesEl.appendChild(wrap);
  scrollToBottom();
  return wrap;
}

function showTyping() {
  hideWelcome();
  const wrap = document.createElement("div");
  wrap.className = "message assistant typing";
  wrap.innerHTML = `
    <div class="msg-avatar assistant"><img src="${LOGO}" alt="ZORA" /></div>
    <div class="msg-content">
      <div class="bubble"><span></span><span></span><span></span></div>
    </div>`;
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
      body: JSON.stringify({ message: text, conversation_id: currentConversationId }),
    });

    typing.remove();

    if (res.status === 401) {
      location.href = "/login";
      return;
    }

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      addMessage(err.detail || `Request failed (${res.status}).`, "error");
      return;
    }

    const data = await res.json();
    const wasNew = currentConversationId !== data.conversation_id;
    currentConversationId = data.conversation_id;
    addMessage(data.response, "assistant");

    // Refresh the sidebar so the new/updated conversation appears on top.
    if (wasNew) loadConversations();
    else markActive();
  } catch (e) {
    typing.remove();
    addMessage("Network error — is the server running?", "error");
  } finally {
    sendBtn.disabled = false;
    input.focus();
  }
}

// Anonymous visitors can view the chat, but sending requires signing in.
function promptSignIn() {
  addMessage("🔒 Please sign in to start chatting. Taking you to the login page…", "error");
  setTimeout(() => {
    location.href = "/login";
  }, 1200);
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text) return;

  if (!isAuthenticated) {
    promptSignIn();
    return;
  }

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

// Delegated clicks in the messages area: suggestion chips + copy buttons.
messagesEl.addEventListener("click", (e) => {
  const chip = e.target.closest(".chip");
  if (chip) {
    input.value = chip.textContent.trim();
    autoGrow();
    input.focus();
    form.requestSubmit();
    return;
  }

  const copyBtn = e.target.closest(".copy-btn");
  if (copyBtn) {
    const bubble = copyBtn.parentElement.querySelector(".bubble");
    navigator.clipboard.writeText(bubble.dataset.raw || bubble.textContent).then(() => {
      const label = copyBtn.querySelector(".copy-label");
      copyBtn.classList.add("copied");
      label.textContent = "Copied!";
      setTimeout(() => {
        label.textContent = "Copy";
        copyBtn.classList.remove("copied");
      }, 1500);
    });
  }
});

// Delegated clicks in the sidebar: open a conversation or delete it.
convListEl.addEventListener("click", (e) => {
  const del = e.target.closest(".conv-del");
  if (del) {
    e.stopPropagation();
    deleteConversation(Number(del.dataset.del));
    return;
  }
  const item = e.target.closest(".conv-item");
  if (item) openConversation(Number(item.dataset.id));
});

// --------------------------------------------------------------------------- //
// Theme (dark / light)
// --------------------------------------------------------------------------- //

const THEME_KEY = "zora-theme";
const SUN_ICON =
  '<circle cx="12" cy="12" r="4"></circle><line x1="12" y1="2" x2="12" y2="5"></line><line x1="12" y1="19" x2="12" y2="22"></line><line x1="2" y1="12" x2="5" y2="12"></line><line x1="19" y1="12" x2="22" y2="12"></line><line x1="4.9" y1="4.9" x2="7" y2="7"></line><line x1="17" y1="17" x2="19.1" y2="19.1"></line><line x1="4.9" y1="19.1" x2="7" y2="17"></line><line x1="17" y1="7" x2="19.1" y2="4.9"></line>';
const MOON_ICON = '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path>';

function applyTheme(theme) {
  const light = theme === "light";
  if (light) {
    document.documentElement.setAttribute("data-theme", "light");
  } else {
    document.documentElement.removeAttribute("data-theme");
  }
  // The switch is ON when light mode is active.
  const toggle = document.getElementById("theme-toggle");
  const label = document.getElementById("theme-label");
  const icon = document.getElementById("theme-icon");
  if (toggle) toggle.setAttribute("aria-checked", light ? "true" : "false");
  if (label) label.textContent = "Light mode";
  if (icon) icon.innerHTML = light ? SUN_ICON : MOON_ICON;
}

applyTheme(localStorage.getItem(THEME_KEY) || "dark");

const themeToggle = document.getElementById("theme-toggle");
themeToggle.addEventListener("click", () => {
  const next =
    (localStorage.getItem(THEME_KEY) || "dark") === "light" ? "dark" : "light";
  localStorage.setItem(THEME_KEY, next);
  applyTheme(next);
});

// Account dropdown (click the header avatar).
const headerUserBtn = document.getElementById("header-user");
const userMenu = document.getElementById("user-menu");

headerUserBtn.addEventListener("click", (e) => {
  e.stopPropagation();
  userMenu.hidden = !userMenu.hidden;
});

// --------------------------------------------------------------------------- //
// Sign out
// --------------------------------------------------------------------------- //

// Handle logout in JS so it's reliable regardless of page caching: clear the
// server session, drop local state, then *replace* history so the Back button
// can't restore the (bfcache'd) authenticated page and make it look like the
// sign-out failed.
async function doLogout(e) {
  if (e) e.preventDefault();
  isAuthenticated = false;
  currentUser = null;
  try {
    await fetch("/auth/logout", { credentials: "same-origin", cache: "no-store" });
  } catch (_) {
    /* network hiccup — still send the user to the login page */
  }
  location.replace("/login");
}

// Both sign-out controls (header menu + sidebar footer) route through here.
for (const el of document.querySelectorAll('a[href="/auth/logout"]')) {
  el.addEventListener("click", doLogout);
}

// If the browser restores this page from the back/forward cache after a
// sign-out, force a fresh load so we never show stale, logged-in content.
window.addEventListener("pageshow", (e) => {
  if (e.persisted) location.reload();
});

document.addEventListener("click", (e) => {
  if (!userMenu.hidden && !e.target.closest(".header-user-wrap")) {
    userMenu.hidden = true;
  }
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") userMenu.hidden = true;
});

newChatBtn.addEventListener("click", startNewChat);

sidebarToggle.addEventListener("click", () => {
  sidebar.hidden = !sidebar.hidden;
});

// Initial render.
renderWelcome();
loadUser();
input.focus();
