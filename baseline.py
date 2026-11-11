"""The no-tools baseline for the README: the same model, no system prompt, no tools.

Run with: uv run baseline.py
"""

import asyncio

import litellm

from desk.config import MODEL_NAME, VERTEX

QUESTIONS = [
    "Are married people happier than single people?",
    "Why do Nordic countries top the happiness rankings?",
    "How much does seeing friends matter for happiness?",
]


async def main() -> None:
    for question in QUESTIONS:
        response = await litellm.acompletion(
            model=MODEL_NAME, messages=[{"role": "user", "content": question}], **VERTEX
        )
        print(f"\n=== {question}\n{response.choices[0].message.content}")


if __name__ == "__main__":
    asyncio.run(main())
