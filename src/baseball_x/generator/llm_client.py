"""Claude APIクライアント（prompt caching対応）。"""
import anthropic

from baseball_x.config import settings
from baseball_x.generator.prompts import SYSTEM_PROMPT

_client: anthropic.Anthropic | None = None

MODEL = "claude-haiku-4-5"
MAX_TOKENS = 300


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return _client


def generate_draft(user_prompt: str) -> str:
    """下書きテキストを生成して返す。

    システムプロンプトはprompt cachingを有効化してコストを削減する。
    """
    client = get_client()
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},  # prompt caching
            }
        ],
        messages=[{"role": "user", "content": user_prompt}],
    )
    return response.content[0].text.strip()
