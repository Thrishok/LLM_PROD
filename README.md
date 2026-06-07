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

# Add your Groq API key
cp .env.example .env        # then edit .env and set GROQ_API_KEY
```

## Run

```bash
python main.py
```

Then open http://127.0.0.1:8000 in your browser.

## API

| Method | Path                      | Body / Notes                                   |
| ------ | ------------------------- | ---------------------------------------------- |
| POST   | `/api/chat`               | `{"message": "...", "session_id": "optional"}` |
| DELETE | `/api/session/{id}`       | Clears a conversation's history                |
| GET    | `/api/health`             | Health + whether `GROQ_API_KEY` is set         |

`POST /api/chat` returns `{"response": "...", "session_id": "..."}`. Omit
`session_id` on the first call to start a new conversation, then send the
returned id on subsequent calls to keep context.
