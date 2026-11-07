"""
llm.py - the ONE place in the project that talks to OpenRouter.

Everything else calls call_structured(). If the AI is slow, down, or returns
nonsense, this raises LLMError and the caller decides what to do instead.
"""
import json
import os

import httpx
from dotenv import load_dotenv

load_dotenv()  # reads your .env file (if there is one) so the API key stays out of the code

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "~anthropic/claude-sonnet-latest"  # any slug from openrouter.ai/models works


class LLMError(Exception):
    """Anything that goes wrong when asking the AI."""


def is_configured() -> bool:
    return bool(os.environ.get("OPENROUTER_API_KEY"))


def model_name() -> str:
    return os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL)


def max_tokens() -> int:
    """
    The longest reply we allow. OpenRouter reserves this much of your balance up
    front, so without a limit it reserves the model's huge default and can refuse
    the request on a small balance. Our JSON answers are tiny.
    """
    try:
        return max(200, int(os.environ.get("OPENROUTER_MAX_TOKENS", "2000")))
    except ValueError:
        return 2000


def _parse_json(content: str):
    text = content.strip()
    if text.startswith("```"):  # some models wrap JSON in a code fence
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    return json.loads(text)


def call_structured(system: str, user: str, schema_name: str, schema: dict, timeout: float = 25.0):
    """
    Ask the AI a question and get back JSON that follows `schema`.
    The schema tells the model what shape to answer in. We STILL validate the
    result ourselves afterwards, because a schema is a request, not a guarantee.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise LLMError("OPENROUTER_API_KEY is not set")

    payload = {
        "model": model_name(),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens(),
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": schema_name, "strict": True, "schema": schema},
        },
        # Only use providers that really support structured outputs.
        "provider": {"require_parameters": True},
    }
    try:
        response = httpx.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "X-OpenRouter-Title": "Smart Kitchen Hackathon",
            },
            json=payload,
            timeout=timeout,
        )
    except httpx.HTTPError as e:
        raise LLMError(f"Could not reach OpenRouter ({type(e).__name__})") from e

    if response.status_code != 200:
        raise LLMError(f"OpenRouter returned status {response.status_code}: {response.text[:200]}")
    try:
        return _parse_json(response.json()["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError, ValueError, AttributeError) as e:
        raise LLMError("The AI's reply could not be read as JSON") from e