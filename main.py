"""FastAPI chat application backed by a LangGraph agent.
Converted from main.ipynb. The agent uses a Groq-hosted LLM with a single
DuckDuckGo web-search tool, exposed over an HTTP API plus a small web UI.
"""

import os
from typing import Annotated, List

from typing_extensions import TypedDict

from dotenv import load_dotenv


load_dotenv()
load_dotenv(dotenv_path='.env')


from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from pydantic import BaseModel, Field

import auth
import db
from auth import current_user
from db import init_db

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_groq import ChatGroq
from langgraph.graph import START, END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from groq import BadRequestError
from functools import lru_cache

from ddgs import DDGS

# --------------------------------------------------------------------------- #
# Agent graph (ported from the notebook)
# --------------------------------------------------------------------------- #


class State(TypedDict):
    messages: Annotated[list, add_messages]



@tool
def duckduckgo_search_tool(query: str, max_results: int = 3) -> str:
    """Search the web via DuckDuckGo for fresh/factual info. Returns formatted results."""
    try:
        with DDGS() as ddg:
            results = list(ddg.text(query, max_results=max_results))
    except Exception as e:
        return f"Search failed: {e}"

    if not results:
        return f"No results found for: {query}"

    return "\n\n".join(
        f"{r.get('title', '')}\n{r.get('href', '')}\n{r.get('body', '')}"
        for r in results
    )


tools = [duckduckgo_search_tool]

# Minimal schema tool for TPM-limited models (same function, stripped description)
from langchain_core.tools import StructuredTool
import pydantic

class _SearchInput(pydantic.BaseModel):
    query: str

_search_minimal = StructuredTool(
    name="duckduckgo_search_tool",
    description="Search the web.",
    args_schema=_SearchInput,
    func=lambda query: duckduckgo_search_tool.invoke({"query": query, "max_results": 3}),
)
tools_minimal = [_search_minimal]

SYSTEM_PROMPT = """You are a helpful assistant with one tool: duckduckgo_search_tool.

You MUST call duckduckgo_search_tool for: current news, live prices (gold, stocks, crypto), sports scores, weather, or any real-time/recent information. Do not say you cannot access the internet — use the tool instead.

For everything else — coding, explanations, history, opinions, general knowledge — answer directly without calling the tool."""



SUMMARIZE_AFTER = 10
MAX_HISTORY_TURNS = 4


def _summarize(conv_id: int) -> None:
    """Summarize all but the last 10 messages, store summary, delete old messages."""
    history = db.get_history(conv_id)
    to_summarize = history[:-MAX_HISTORY_TURNS * 2]
    if not to_summarize:
        return

    existing_summary = db.get_summary(conv_id)
    transcript = "\n".join(f"{role.upper()}: {content}" for role, content in to_summarize)
    prompt = f"""Summarize the following conversation into concise bullet points capturing key facts, decisions, and context. Be brief.

{f'Previous summary:{chr(10)}{existing_summary}{chr(10)}{chr(10)}' if existing_summary else ''}Conversation to summarize:
{transcript}"""

    result = ChatGroq(model_name="llama-3.1-8b-instant", api_key=os.getenv("GROQ_API_KEY"), max_tokens=1024).invoke([HumanMessage(content=prompt)])
    db.set_summary(conv_id, result.content)
    db.delete_old_messages(conv_id, keep_last=MAX_HISTORY_TURNS * 2)


@lru_cache(maxsize=8)
def build_graph(model_name: str, max_tokens: int):
    gpt_oss = model_name in ("openai/gpt-oss-120b", "openai/gpt-oss-20b")
    _llm = ChatGroq(model_name=model_name, api_key=os.getenv("GROQ_API_KEY"), max_tokens=max_tokens)
    _tool_list = tools_minimal if gpt_oss else tools
    _llm_bound = _llm.bind_tools(_tool_list)

    def _tool_calling_node(state: State):
        messages = [SystemMessage(content=SYSTEM_PROMPT)] + state["messages"]
        return {"messages": [_llm_bound.invoke(messages)]}

    builder = StateGraph(State)
    builder.add_node("tool_calling_node", _tool_calling_node)
    builder.add_node("tools", ToolNode(_tool_list))
    builder.add_edge(START, "tool_calling_node")
    builder.add_conditional_edges("tool_calling_node", tools_condition)
    builder.add_edge("tools", "tool_calling_node")

    return builder.compile()


graph = build_graph("llama-3.3-70b-versatile", 4096)

# --------------------------------------------------------------------------- #
# FastAPI app
# --------------------------------------------------------------------------- #

app = FastAPI(title="LLM_PROD Chat", description="LangGraph + Groq chatbot")

# Signed-cookie sessions (required by Authlib's OAuth flow and used to keep
# users logged in). SESSION_SECRET must be a long random string in production.
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET", "dev-only-insecure-change-me"),
    https_only=os.getenv("COOKIE_SECURE", "").lower() == "true",
    same_site="lax",
)

# Auth routes: /auth/login, /auth/callback, /auth/logout
app.include_router(auth.router)


@app.on_event("startup")
def _startup() -> None:
    init_db()

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, description="User message")
    conversation_id: int | None = Field(
        None, description="Conversation to continue; omit to start a new one"
    )
    model: str = Field(default="llama-3.3-70b-versatile", description="Groq model to use")


class ChatResponse(BaseModel):
    response: str
    conversation_id: int
    title: str


def _title_from(message: str) -> str:
    t = message.strip().replace("\n", " ")
    return t[:40] + "…" if len(t) > 40 else t


@app.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest, user: dict = Depends(current_user)):
    if not os.getenv("GROQ_API_KEY"):
        raise HTTPException(
            status_code=500,
            detail="GROQ_API_KEY is not set. Add it to your .env file.",
        )

    # Resolve the conversation: continue an owned one, else start a new one.
    conv_id = req.conversation_id
    is_new = conv_id is None or not db.conversation_owner(conv_id, user["id"])
    if is_new:
        conv_id = db.create_conversation(user["id"])

    MAX_TOKENS = {
        "llama-3.1-8b-instant": 4096,
        "llama-3.3-70b-versatile": 4096,
        "openai/gpt-oss-20b": 4096,
        "openai/gpt-oss-120b": 4096,
    }
    max_tokens = MAX_TOKENS.get(req.model, 8192)
    request_graph = build_graph(req.model, max_tokens)

    # Trigger summarization if conversation has grown large.
    if db.count_messages(conv_id) > SUMMARIZE_AFTER:
        _summarize(conv_id)

    # Smaller context window for TPM-limited models.
    MODEL_MAX_HISTORY = {
        "openai/gpt-oss-120b": 4,
        "openai/gpt-oss-20b": 6,
    }
    max_turns = MODEL_MAX_HISTORY.get(req.model, MAX_HISTORY_TURNS)

    # Rebuild the LLM context from stored history (restart-safe, per user).
    history: List[BaseMessage] = []
    summary = db.get_summary(conv_id)
    if summary:
        history.append(SystemMessage(content=f"Summary of earlier conversation:\n{summary}"))
    for role, content in db.get_history(conv_id)[-max_turns * 2:]:
        history.append(
            HumanMessage(content=content) if role == "user" else AIMessage(content=content)
        )
    history.append(HumanMessage(content=req.message))

    try:
        result = request_graph.invoke({"messages": history})
    except BadRequestError:
        # Don't leave an empty conversation behind if the very first turn fails.
        if is_new:
            db.delete_conversation(conv_id, user["id"])
        raise HTTPException(
            status_code=400,
            detail="The assistant tried to use a capability that isn't available. "
            "Please rephrase your request.",
        )
    except Exception as e:
        if is_new:
            db.delete_conversation(conv_id, user["id"])
        raise HTTPException(
            status_code=502,
            detail=f"The language model request failed: {e}",
        )

    answer = result["messages"][-1].content

    # Persist the turn; name a brand-new conversation after its first message.
    # Truncate long assistant replies before storing to prevent token bloat on replay.
    MAX_STORED_CHARS = 2000
    stored_answer = answer[:MAX_STORED_CHARS] + "…[truncated]" if len(answer) > MAX_STORED_CHARS else answer
    title = db.append_turn(
        conv_id,
        req.message,
        stored_answer,
        _title_from(req.message) if is_new else None,
    )

    return ChatResponse(response=answer, conversation_id=conv_id, title=title)


@app.get("/api/conversations")
def list_conversations(user: dict = Depends(current_user)):
    """All of the current user's conversations, newest first."""
    return db.list_conversations(user["id"])


@app.get("/api/conversations/{conv_id}")
def get_conversation(conv_id: int, user: dict = Depends(current_user)):
    """A single conversation with its full message history."""
    data = db.get_conversation_owned(conv_id, user["id"])
    if data is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return data


@app.delete("/api/conversations/{conv_id}")
def delete_conversation(conv_id: int, user: dict = Depends(current_user)):
    if not db.delete_conversation(conv_id, user["id"]):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"ok": True}


@app.get("/api/me")
def me(user: dict = Depends(current_user)):
    """Return the logged-in user's profile for the frontend."""
    return user


@app.get("/api/health")
def health():
    return {"status": "ok", "groq_api_key_set": bool(os.getenv("GROQ_API_KEY"))}


# Serve the frontend. Mounted last so it doesn't shadow the /api routes.
static_dir = os.path.join(os.path.dirname(__file__), "static")


@app.get("/")
def index():
    # Everyone can view the chat UI. Sending a message still requires auth
    # (the /api/chat endpoint enforces it), so anonymous users are prompted
    # to log in only when they actually try to chat.
    return FileResponse(os.path.join(static_dir, "index.html"))


@app.get("/login")
def login_page(request: Request):
    # Already signed in? Skip the login screen.
    if request.session.get("user"):
        return RedirectResponse(url="/")
    return FileResponse(os.path.join(static_dir, "login.html"))


@app.get("/terms")
def terms_page():
    return FileResponse(os.path.join(static_dir, "terms.html"))


app.mount("/", StaticFiles(directory=static_dir), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
