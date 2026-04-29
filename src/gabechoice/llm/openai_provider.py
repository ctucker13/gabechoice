import json
import openai
from pydantic import BaseModel

from gabechoice.config import settings


class OpenAIProvider:
    def __init__(self) -> None:
        self._client = openai.AsyncOpenAI(api_key=settings.openai_api_key)

    async def complete_json(
        self,
        system: str,
        user: str,
        response_model: type[BaseModel],
    ) -> BaseModel:
        schema = response_model.model_json_schema()
        response = await self._client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": response_model.__name__,
                    "schema": schema,
                    "strict": True,
                },
            },
        )
        raw = response.choices[0].message.content
        return response_model.model_validate(json.loads(raw))
