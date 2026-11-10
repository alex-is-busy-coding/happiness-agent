"""The no-tools baseline for the README: the same model, no system prompt, no tools.

Run with: uv run baseline.py
"""

import asyncio
import os
from pathlib import Path

import litellm
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

QUESTIONS = [
    "Are married people happier than single people?",
    "Why do Nordic countries top the happiness rankings?",
    "How much does seeing friends matter for happiness?",
]


async def main() -> None:
    for question in QUESTIONS:
        response = await litellm.acompletion(
            model="vertex_ai/gemini-3.5-flash",
            messages=[{"role": "user", "content": question}],
            vertex_project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
            vertex_location="global",
        )
        print(f"\n=== {question}\n{response.choices[0].message.content}")


if __name__ == "__main__":
    asyncio.run(main())
