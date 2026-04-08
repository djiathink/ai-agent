import anthropic
from config import settings

# Max messages kept in history per user (pairs of user+assistant)
MAX_HISTORY_PAIRS = 20


class Agent:
    def __init__(self):
        self.client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        self.history: list[dict] = []

    async def chat(self, user_message: str) -> str:
        self.history.append({"role": "user", "content": user_message})

        # Trim history to avoid exceeding token limits
        if len(self.history) > MAX_HISTORY_PAIRS * 2:
            self.history = self.history[-(MAX_HISTORY_PAIRS * 2):]

        try:
            response = await self.client.messages.create(
                model=settings.claude_model,
                max_tokens=settings.max_tokens,
                system=settings.system_prompt,
                messages=self.history,
            )
            reply = response.content[0].text
        except Exception as e:
            reply = f"Erreur: {e}"
            # Don't keep the failed user message in history
            self.history.pop()
            return reply

        self.history.append({"role": "assistant", "content": reply})
        return reply
