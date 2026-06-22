// Apply the saved theme (shared with the chat page) before anything renders.
if ((localStorage.getItem("zora-theme") || "dark") === "light") {
  document.documentElement.setAttribute("data-theme", "light");
}

const form = document.getElementById("auth-form");
const errorBox = document.getElementById("error");
const submitBtn = document.getElementById("submit-btn");
const toggleText = document.getElementById("toggle-text");
const toggleLink = document.getElementById("toggle-link");
const subtitle = document.getElementById("subtitle");
const nameField = document.getElementById("name-field");
const nameInput = document.getElementById("name");
const passwordInput = document.getElementById("password");
const togglePassword = document.getElementById("toggle-password");

let mode = "login"; // "login" | "register"

// Show / hide the password when the eye icon is clicked.
togglePassword.addEventListener("click", () => {
  const reveal = passwordInput.type === "password";
  passwordInput.type = reveal ? "text" : "password";
  togglePassword.classList.toggle("revealed", reveal);
  const label = reveal ? "Hide password" : "Show password";
  togglePassword.setAttribute("aria-label", label);
  togglePassword.setAttribute("title", label);
  passwordInput.focus();
});

function showError(msg) {
  errorBox.textContent = msg;
  errorBox.hidden = false;
}

function clearError() {
  errorBox.hidden = true;
}

function applyMode() {
  if (mode === "login") {
    subtitle.textContent = "Sign in to start chatting with your AI assistant.";
    submitBtn.textContent = "Sign in";
    toggleText.textContent = "Don't have an account?";
    toggleLink.textContent = "Create one";
    nameField.hidden = true;
    passwordInput.setAttribute("autocomplete", "current-password");
  } else {
    subtitle.textContent = "Create an account to get started.";
    submitBtn.textContent = "Create account";
    toggleText.textContent = "Already have an account?";
    toggleLink.textContent = "Sign in";
    nameField.hidden = false;
    passwordInput.setAttribute("autocomplete", "new-password");
  }
  clearError();
}

toggleLink.addEventListener("click", (e) => {
  e.preventDefault();
  mode = mode === "login" ? "register" : "login";
  applyMode();
});

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  clearError();

  const email = document.getElementById("email").value.trim();
  const password = passwordInput.value;
  const name = nameInput.value.trim();

  if (mode === "register" && password.length < 8) {
    showError("Password must be at least 8 characters.");
    return;
  }

  const url = mode === "login" ? "/auth/login-password" : "/auth/register";
  const body = mode === "login" ? { email, password } : { name, email, password };

  submitBtn.disabled = true;
  submitBtn.textContent = mode === "login" ? "Signing in…" : "Creating…";

  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    if (res.ok) {
      location.href = "/";
      return;
    }

    const data = await res.json().catch(() => ({}));
    showError(data.detail || `Request failed (${res.status}).`);
  } catch (_) {
    showError("Network error — is the server running?");
  } finally {
    submitBtn.disabled = false;
    applyMode();
  }
});

// Surface OAuth errors passed back as ?error=...
const params = new URLSearchParams(location.search);
const err = params.get("error");
if (err) {
  showError(
    err === "denied"
      ? "Google sign-in was cancelled. Please try again."
      : err === "unverified"
      ? "Your Google email isn't verified."
      : "Something went wrong signing you in."
  );
}

applyMode();
