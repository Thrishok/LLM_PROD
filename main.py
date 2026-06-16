"""FastAPI chat application backed by a LangGraph agent.

Converted from main.ipynb. The agent uses a Groq-hosted LLM with a single
DuckDuckGo web-search tool, exposed over an HTTP API plus a small web UI.
"""

import os
import uuid
from typing import Annotated, Dict, List

from typing_extensions import TypedDict

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from pydantic import BaseModel, Field

import auth
from auth import current_user
from db import init_db

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_groq import ChatGroq
from langgraph.graph import START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from groq import BadRequestError

from ddgs import DDGS

load_dotenv()

# --------------------------------------------------------------------------- #
# Agent graph (ported from the notebook)
# --------------------------------------------------------------------------- #


class State(TypedDict):
    messages: Annotated[list, add_messages]


llm = ChatGroq(model_name="openai/gpt-oss-120b", api_key=os.getenv("GROQ_API_KEY"))


@tool
def duckduckgo_search_tool(query: str, max_results: int = 5) -> str:
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
llm_with_tools = llm.bind_tools(tools)

SYSTEM_PROMPT = """You are a helpful assistant with one tool: duckduckgo_search_tool(query, max_results).

Use it ONLY when you need fresh, real-world, or factual info you don't already know (news, scores, prices, current events, recent facts). For chit-chat, opinions, or things you already know, answer directly without calling it.

When you call the tool, summarise the returned results clearly and cite nothing you didn't get back. Do not invent facts. Do not call any tool other than duckduckgo_search_tool."""


def tool_calling_node(state: State):
    messages = [SystemMessage(content=SYSTEM_PROMPT)] + state["messages"]
    response = llm_with_tools.invoke(messages)
    return {"messages": [response]}


def build_graph():
    builder = StateGraph(State)
    builder.add_node("tool_calling_node", tool_calling_node)
    builder.add_node("tools", ToolNode(tools))

    builder.add_edge(START, "tool_calling_node")
    builder.add_conditional_edges("tool_calling_node", tools_condition)
    builder.add_edge("tools", "tool_calling_node")

    return builder.compile()


graph = build_graph()

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

# In-memory conversation history keyed by session id. Fine for a single-process
# demo; swap for a real store (Redis, DB) before scaling out.
sessions: Dict[str, List[BaseMessage]] = {}


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, description="User message")
    session_id: str | None = Field(
        None, description="Conversation id; omit to start a new conversation"
    )


class ChatResponse(BaseModel):
    response: str
    session_id: str


@app.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest, user: dict = Depends(current_user)):
    if not os.getenv("GROQ_API_KEY"):
        raise HTTPException(
            status_code=500,
            detail="GROQ_API_KEY is not set. Add it to your .env file.",
        )

    session_id = req.session_id or str(uuid.uuid4())
    history = sessions.get(session_id, [])
    history.append(HumanMessage(content=req.message))

    try:
        result = graph.invoke({"messages": history})
    except BadRequestError:
        # Roll back the user message that triggered the failure.
        history.pop()
        sessions[session_id] = history
        raise HTTPException(
            status_code=400,
            detail="The assistant tried to use a capability that isn't available. "
            "Please rephrase your request.",
        )
    except Exception as e:
        # Network/auth/upstream errors: roll back and surface a clean message
        # instead of a bare 500 traceback.
        history.pop()
        sessions[session_id] = history
        raise HTTPException(
            status_code=502,
            detail=f"The language model request failed: {e}",
        )

    # Persist the full message list returned by the graph (includes the new
    # AI/tool messages) so the next turn has the complete context.
    sessions[session_id] = result["messages"]
    answer = result["messages"][-1].content

    return ChatResponse(response=answer, session_id=session_id)


@app.delete("/api/session/{session_id}")
def reset_session(session_id: str, user: dict = Depends(current_user)):
    """Clear a conversation's history."""
    sessions.pop(session_id, None)
    return {"status": "cleared", "session_id": session_id}


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
def index(request: Request):
    # Gate the chat UI behind login: send anonymous visitors to the login page.
    if not request.session.get("user"):
        return RedirectResponse(url="/login")
    return FileResponse(os.path.join(static_dir, "index.html"))


@app.get("/login")
def login_page(request: Request):
    # Already signed in? Skip the login screen.
    if request.session.get("user"):
        return RedirectResponse(url="/")
    return FileResponse(os.path.join(static_dir, "login.html"))


app.mount("/", StaticFiles(directory=static_dir), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
