import httpx
from fastapi import FastAPI, Request, Response
from contextlib import asynccontextmanager

from config import settings
from agent import Agent

# One agent instance per user (keyed by telegram user_id)
agents: dict[int, Agent] = {}

TELEGRAM_API = f"https://api.telegram.org/bot{settings.telegram_token}"


async def send_message(chat_id: int, text: str) -> None:
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": chat_id, "text": text},
        )


async def set_webhook(base_url: str) -> None:
    webhook_url = f"{base_url}/webhook"
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{TELEGRAM_API}/setWebhook",
            json={"url": webhook_url},
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.base_url:
        await set_webhook(settings.base_url)
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/")
async def health():
    return {"status": "ok"}


@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()

    message = data.get("message") or data.get("edited_message")
    if not message:
        return Response(status_code=200)

    chat_id: int = message["chat"]["id"]
    user_id: int = message["from"]["id"]
    text: str = message.get("text", "").strip()

    if not text:
        return Response(status_code=200)

    # /start command
    if text == "/start":
        agents.pop(user_id, None)
        await send_message(chat_id, settings.welcome_message)
        return Response(status_code=200)

    # /reset command
    if text == "/reset":
        agents.pop(user_id, None)
        await send_message(chat_id, "Conversation réinitialisée.")
        return Response(status_code=200)

    # Get or create agent for this user
    if user_id not in agents:
        agents[user_id] = Agent()

    reply = await agents[user_id].chat(text)
    await send_message(chat_id, reply)

    return Response(status_code=200)
