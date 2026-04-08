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
            kwargs = dict(
                model=settings.claude_model,
                max_tokens=settings.max_tokens,
                system=settings.system_prompt,
                messages=self.history,
            )

            if settings.mcp_server_url:
                response = await self.client.beta.messages.create(
                    **kwargs,
                    betas=["mcp-client-2025-04-04"],
                    extra_body={
                        "mcp_servers": [
                            {
                                "type": "url",
                                "url": settings.mcp_server_url,
                                "name": settings.mcp_server_name,
                            }
                        ]
                    },
                )
            else:
                response = await self.client.messages.create(**kwargs)

            # Extract all text blocks (MCP may return multiple content blocks)
            reply = "\n".join(
                block.text for block in response.content
                if hasattr(block, "text") and block.text is not None
            ) or "Je n'ai pas pu générer une réponse."

        except Exception as e:
            reply = f"Erreur: {e}"
            self.history.pop()
            return reply

        self.history.append({"role": "assistant", "content": reply})
        return reply
