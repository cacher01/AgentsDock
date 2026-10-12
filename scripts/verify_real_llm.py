import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings
from app.llm.client import OpenAICompatibleClient


async def main() -> None:
    settings = Settings.from_env_file()
    print("api_key_loaded", bool(settings.llm_api_key))
    print("model", settings.llm_model)
    response = await OpenAICompatibleClient(settings).chat(
        [{"role": "user", "content": "Return only: ok"}],
        max_tokens=64,
    )
    print("content", repr(response.content[:120]))
    if response.raw:
        print("raw_keys", sorted(response.raw.keys()))


if __name__ == "__main__":
    asyncio.run(main())
