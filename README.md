# LLM_PROD

Production version of the chatbot — a LangGraph + Groq agent with a DuckDuckGo
web-search tool, served via FastAPI with a simple web chat UI.

## Architecture

- **`main.py`** — FastAPI app. Builds the LangGraph agent (`tool_calling_node`
  + `ToolNode`) and exposes it over HTTP. Conversation history is kept in memory
  per `session_id`.
- **`static/`** — frontend chat UI (plain HTML/CSS/JS, no build step).

## Setup

```bash
pip install -r requirements.txt

cp .env.example .env
```

Edit `.env` with your credentials:

- `GROQ_API_KEY` — your Groq API key
- `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` — Google OAuth credentials
- `OAUTH_REDIRECT_URI` — the callback URL for Google sign-in
- `SESSION_SECRET` — a long random string for signed session cookies
- `COOKIE_SECURE=true` in production when using HTTPS

For local development, use:

```env
OAUTH_REDIRECT_URI=http://localhost:8000/auth/callback
```

On Render, set `OAUTH_REDIRECT_URI` to your deployed app URL:

```env
OAUTH_REDIRECT_URI=https://<your-app>.onrender.com/auth/callback
```

## Run

```bash
python main.py
```

Then open http://127.0.0.1:8000 in your browser.

## Authentication

The app supports Google OAuth sign-in. Users are created automatically on first successful Google login and the signed session stores a minimal authenticated profile.

## API

| Method | Path                       | Notes |
| ------ | -------------------------- | ----- |
| POST   | `/api/chat`                | `{"message": "...", "conversation_id": <optional>}` |
| GET    | `/api/conversations`       | List conversations for the authenticated user |
| GET    | `/api/conversations/{id}`  | Get a single conversation owned by the user |
| DELETE | `/api/conversations/{id}`  | Delete a user's conversation |
| GET    | `/api/me`                  | Get the authenticated user's profile |
| GET    | `/api/health`              | Health check + Groq API key status |

`POST /api/chat` returns `{"response": "...", "conversation_id": ... , "title": "..."}`. Omit `conversation_id` on the first call to start a new chat, then send it on later calls to continue the same session.
