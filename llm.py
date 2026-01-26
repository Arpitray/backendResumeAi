import os
import httpx
from dotenv import load_dotenv

load_dotenv()
async def call_answer_llm(prompt: str) -> str:
    api_key = os.getenv("Mistral_API_KEY")

    if not api_key:
        raise RuntimeError("Mistral_API_KEY not set")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost",
        "X-Title": "AI-Career-Agent",
    }
    payload = {
        "model": "mistralai/devstral-2512:free",
        "messages": [
            {
                "role": "system",
                "content": "You are an AI career assistant. Answer clearly and cite sources when possible.",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
    }
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=payload,
        )

    data = response.json()

    if "choices" not in data:
        raise RuntimeError(f"LLM error: {data}")

    return data["choices"][0]["message"]["content"]
