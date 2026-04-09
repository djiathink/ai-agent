import asyncio
import anthropic
import openai
from config import settings

MAX_HISTORY_PAIRS = 20
MAX_RETRIES = 3
RETRY_DELAY = 5  # seconds


class Agent:
    def __init__(self):
        self.client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        self.history: list[dict] = []

    async def _create_anthropic(self, **kwargs):
        """Call Anthropic API with retry on overload (529)."""
        for attempt in range(MAX_RETRIES):
            try:
                if settings.mcp_server_url:
                    return await self.client.beta.messages.create(
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
                    return await self.client.messages.create(**kwargs)
            except anthropic.InternalServerError as e:
                if "overloaded" in str(e).lower() and attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(RETRY_DELAY * (attempt + 1))
                    continue
                raise

    async def _create_openai(self, messages: list, system: str, max_tokens: int) -> str:
        """Fallback to OpenAI GPT (no MCP tools)."""
        oai = openai.AsyncOpenAI(api_key=settings.openai_api_key)
        oai_messages = [{"role": "system", "content": system}] + messages
        response = await oai.chat.completions.create(
            model="gpt-4o",
            max_tokens=max_tokens,
            messages=oai_messages,
        )
        return response.choices[0].message.content or ""

    async def chat(self, user_message: str) -> str:
        self.history.append({"role": "user", "content": user_message})

        if len(self.history) > MAX_HISTORY_PAIRS * 2:
            self.history = self.history[-(MAX_HISTORY_PAIRS * 2):]

        kwargs = dict(
            model=settings.claude_model,
            max_tokens=settings.max_tokens,
            system=settings.system_prompt,
            messages=self.history,
        )

        try:
            response = await self._create_anthropic(**kwargs)

            reply = "\n".join(
                block.text for block in response.content
                if hasattr(block, "text") and block.text is not None
            ).strip()

            if not reply and any(
                getattr(b, "type", "") in ("tool_use", "mcp_tool_use")
                for b in response.content
            ):
                self.history.append({"role": "assistant", "content": response.content})
                followup = await self._create_anthropic(**kwargs | {"messages": self.history})
                reply = "\n".join(
                    block.text for block in followup.content
                    if hasattr(block, "text") and block.text is not None
                ).strip() or "Commande transmise."
                self.history.pop()
            elif not reply:
                reply = "Je n'ai pas pu générer une réponse."

        except anthropic.InternalServerError as e:
            if "overloaded" in str(e).lower() and settings.openai_api_key:
                print("[WARN] Anthropic overloaded, falling back to OpenAI")
                try:
                    reply = await self._create_openai(
                        self.history, settings.system_prompt, settings.max_tokens
                    )
                    reply = reply or "Je n'ai pas pu générer une réponse."
                except Exception as oai_err:
                    print(f"[ERROR] OpenAI fallback failed: {oai_err}")
                    self.history.pop()
                    return "Le service est momentanément indisponible. Veuillez réessayer dans quelques instants."
            else:
                self.history.pop()
                return "Le service est momentanément surchargé. Veuillez réessayer dans quelques instants."
        except anthropic.BadRequestError as e:
            self.history.pop()
            return f"Erreur de requête : {e}"
        except Exception as e:
            print(f"[ERROR] {type(e).__name__}: {e}")
            self.history.pop()
            return f"Erreur: {e}"

        self.history.append({"role": "assistant", "content": reply})
        return reply
