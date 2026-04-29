from typing import Protocol
from pydantic import BaseModel


class LLMProvider(Protocol):
    async def complete_json(
        self,
        system: str,
        user: str,
        response_model: type[BaseModel],
    ) -> BaseModel: ...
