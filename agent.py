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

            # Log content block types for debugging
            block_types = [getattr(b, "type", type(b).__name__) for b in response.content]
            print(f"[DEBUG] stop_reason={response.stop_reason} blocks={block_types}")

            # Extract text blocks
            reply = "\n".join(
                block.text for block in response.content
                if hasattr(block, "text") and block.text is not None
            ).strip()

            # If no text but MCP tools were used, it means Claude called the tool
            # but didn't generate a follow-up text — send a second turn to get it
            if not reply and any(
                getattr(b, "type", "") in ("tool_use", "mcp_tool_use")
                for b in response.content
            ):
                # Append full assistant content (with tool blocks) to history
                self.history.append({"role": "assistant", "content": response.content})

                # Ask Claude to summarize the result
                followup = await self.client.beta.messages.create(
                    model=settings.claude_model,
                    max_tokens=settings.max_tokens,
                    system=settings.system_prompt,
                    messages=self.history,
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
                reply = "\n".join(
                    block.text for block in followup.content
                    if hasattr(block, "text") and block.text is not None
                ).strip() or "Commande transmise."

                self.history.pop()  # remove the tool-blocks assistant turn
            elif not reply:
                reply = "Je n'ai pas pu générer une réponse."

        except Exception as e:
            print(f"[ERROR] {type(e).__name__}: {e}")
            self.history.pop()
            return f"[DEBUG] {type(e).__name__}: {e}"

        self.history.append({"role": "assistant", "content": reply})
        return reply
