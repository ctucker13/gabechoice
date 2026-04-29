import anthropic
from pydantic import BaseModel

from gabechoice.config import settings


class AnthropicProvider:
    def __init__(self) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    async def complete_json(
        self,
        system: str,
        user: str,
        response_model: type[BaseModel],
    ) -> BaseModel:
        schema = response_model.model_json_schema()
        tool = {
            "name": "respond",
            "description": "Return the structured response.",
            "input_schema": schema,
        }
        # Cache the system prompt — it's static per node type and reused across runs.
        response = await self._client.messages.create(
            model=settings.anthropic_model,
            max_tokens=4096,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            tools=[tool],
            tool_choice={"type": "tool", "name": "respond"},
        )
        for block in response.content:
            if block.type == "tool_use":
                return response_model.model_validate(block.input)
        raise RuntimeError("Anthropic returned no tool_use block")
