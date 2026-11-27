import json
import math
import time
from google import genai
from google.genai import types
from groq import Groq
from app.config import settings

# 1. Initialize cloud clients once (zero local RAM used)
gemini_client = genai.Client(api_key=settings.gemini_api_key) if settings.gemini_api_key else None
groq_client = Groq(api_key=settings.groq_api_key) if settings.groq_api_key else None


# 2. Helper to normalize vectors when shrinking from 3072 -> 768 dimensions
def _normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec] if norm > 0 else vec


# 3. Convert text into 768 numbers for pgvector search
def get_embedding(text: str, task_type: str = "RETRIEVAL_QUERY") -> list[float]:
    if not gemini_client:
        raise ValueError("GEMINI_API_KEY is missing in .env")

    result = gemini_client.models.embed_content(
        model=settings.embedding_model,
        contents=text,
        config=types.EmbedContentConfig(
            task_type=task_type,
            output_dimensionality=768,
        ),
    )
    raw_vec = list(result.embeddings[0].values)
    return _normalize(raw_vec)


# 4. Ask Gemini or Groq and force a clean Python dict (JSON) back
def generate_json(
    prompt: str,
    system_prompt: str = "You are a precise logistics operations assistant. Return valid JSON only.",
    provider: str | None = None,
    model: str | None = None,
    max_retries: int = 3,
) -> dict:
    chosen_provider = (provider or settings.answering_provider).lower()
    chosen_model = model or settings.answering_model

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            if chosen_provider == "gemini":
                if not gemini_client:
                    raise ValueError("GEMINI_API_KEY is missing")
                response = gemini_client.models.generate_content(
                    model=chosen_model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=system_prompt,
                        response_mime_type="application/json",
                        temperature=0.1,
                    ),
                )
                return json.loads(response.text)

            elif chosen_provider == "groq":
                if not groq_client:
                    raise ValueError("GROQ_API_KEY is missing")
                response = groq_client.chat.completions.create(
                    model=chosen_model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.1,
                )
                return json.loads(response.choices[0].message.content)

            else:
                raise ValueError(f"Unknown provider: {chosen_provider}")

        except Exception as e:
            last_error = e
            if attempt < max_retries:
                time.sleep(1.5 * attempt)

    raise RuntimeError(f"LLM failed after {max_retries} attempts: {last_error}")