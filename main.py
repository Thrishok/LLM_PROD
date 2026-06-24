"""FastAPI chat application backed by a LangGraph agent.

Converted from main.ipynb. The agent uses a Groq-hosted LLM with a single
DuckDuckGo web-search tool, exposed over an HTTP API plus a small web UI.
"""

import os
from typing import Annotated, List

from typing_extensions import TypedDict

from dotenv import load_dotenv

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
from langgraph.graph import START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from groq import BadRequestError

from ddgs import DDGS

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

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, description="User message")
    conversation_id: int | None = Field(
        None, description="Conversation to continue; omit to start a new one"
    )


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

    # Rebuild the LLM context from stored history (restart-safe, per user).
    history: List[BaseMessage] = []
    for role, content in db.get_history(conv_id):
        history.append(
            HumanMessage(content=content) if role == "user" else AIMessage(content=content)
        )
    history.append(HumanMessage(content=req.message))

    try:
        result = graph.invoke({"messages": history})
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
    title = db.append_turn(
        conv_id,
        req.message,
        answer,
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
