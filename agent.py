import asyncio
import json
import httpx
import openai
import anthropic
from config import settings

MAX_HISTORY_PAIRS = 20
MAX_RETRIES = 3
RETRY_DELAY = 5  # seconds
MAX_TOOL_ITERATIONS = 10


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

    # ─── MCP bridge helpers ───

    async def _mcp_init(self, http: httpx.AsyncClient) -> str:
        """Initialize MCP session, return session_id."""
        resp = await http.post(
            settings.mcp_server_url,
            json={
                "jsonrpc": "2.0",
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "deepseek-bridge", "version": "1.0"},
                },
                "id": 1,
            },
        )
        return resp.headers.get("mcp-session-id", "")

    async def _mcp_list_tools(self, http: httpx.AsyncClient, session_id: str) -> list:
        """Fetch MCP tools and convert to OpenAI function format."""
        resp = await http.post(
            settings.mcp_server_url,
            json={"jsonrpc": "2.0", "method": "tools/list", "params": {}, "id": 2},
            headers={"mcp-session-id": session_id},
        )
        mcp_tools = resp.json().get("result", {}).get("tools", [])
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("inputSchema", {"type": "object", "properties": {}}),
                },
            }
            for t in mcp_tools
        ]

    async def _mcp_call_tool(self, http: httpx.AsyncClient, session_id: str, name: str, arguments: dict) -> str:
        """Call an MCP tool, return text result."""
        resp = await http.post(
            settings.mcp_server_url,
            json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
                "id": 3,
            },
            headers={"mcp-session-id": session_id},
        )
        content = resp.json().get("result", {}).get("content", [])
        return "\n".join(c.get("text", "") for c in content if c.get("type") == "text")

    async def _create_deepseek_with_mcp(self) -> str:
        """Call Deepseek with MCP tool bridge (agentic loop)."""
        ds = openai.AsyncOpenAI(
            api_key=settings.deepseek_api_key,
            base_url="https://api.deepseek.com",
        )

        async with httpx.AsyncClient(timeout=30) as http:
            # Initialize MCP session and fetch tools
            session_id = await self._mcp_init(http)
            tools = await self._mcp_list_tools(http, session_id) if session_id else []

            messages = [{"role": "system", "content": settings.system_prompt}] + self.history

            for _ in range(MAX_TOOL_ITERATIONS):
                kwargs: dict = dict(
                    model="deepseek-chat",
                    max_tokens=settings.max_tokens,
                    messages=messages,
                )
                if tools:
                    kwargs["tools"] = tools
                    kwargs["tool_choice"] = "auto"

                response = await ds.chat.completions.create(**kwargs)
                msg = response.choices[0].message

                if not msg.tool_calls:
                    return msg.content or "Je n'ai pas pu générer une réponse."

                # Execute all tool calls
                messages.append(msg)
                for tc in msg.tool_calls:
                    args = json.loads(tc.function.arguments)
                    print(f"[Deepseek→MCP] {tc.function.name}({args})")
                    result = await self._mcp_call_tool(http, session_id, tc.function.name, args)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })

        return "Je n'ai pas pu compléter l'opération."

    async def chat(self, user_message: str) -> str:
        self.history.append({"role": "user", "content": user_message})

        if len(self.history) > MAX_HISTORY_PAIRS * 2:
            self.history = self.history[-(MAX_HISTORY_PAIRS * 2):]

        # Ensure history ends with a user message (Anthropic requirement)
        while self.history and self.history[-1].get("role") != "user":
            self.history.pop()
        if not self.history:
            self.history.append({"role": "user", "content": user_message})

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

            if not reply:
                reply = "Commande transmise." if any(
                    getattr(b, "type", "") in ("tool_use", "mcp_tool_use")
                    for b in response.content
                ) else "Je n'ai pas pu générer une réponse."

        except anthropic.BadRequestError as e:
            self.history.pop()
            return f"Erreur de requête : {e}"
        except anthropic.InternalServerError as e:
            if "overloaded" in str(e).lower() and settings.deepseek_api_key:
                print("[WARN] Anthropic overloaded, falling back to Deepseek+MCP")
                try:
                    reply = await self._create_deepseek_with_mcp()
                except Exception as ds_err:
                    print(f"[ERROR] Deepseek fallback failed: {ds_err}")
                    self.history.pop()
                    return "Le service est momentanément indisponible. Veuillez réessayer dans quelques instants."
            else:
                self.history.pop()
                return "Le service est momentanément surchargé. Veuillez réessayer dans quelques instants."
        except Exception as e:
            print(f"[ERROR] {type(e).__name__}: {e}")
            self.history.pop()
            return f"Erreur: {e}"

        self.history.append({"role": "assistant", "content": reply})
        return reply
