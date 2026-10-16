import os
from openai import OpenAI

from sonic.config.settings import (
    OPENAI_API_KEY,
    OPENAI_MODEL,
    LMSTUDIO_BASE_URL,
    LMSTUDIO_MODEL,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    FASTAPI_BASE_URL,
    FASTAPI_MODEL,
    OPENROUTER_API_KEY,
    OPENROUTER_MODEL
)

# ── LMStudio ──────────────────────────────────────────────────────────────────
# LMStudio runs a local OpenAI-compatible server
# Default: http://localhost:1234/v1
class LMStudioProvider:
    def __init__(self):
        self.client = OpenAI(
            base_url=LMSTUDIO_BASE_URL,
            api_key="lm-studio"                        # LMStudio ignores this but needs a value
        )
        self.model = LMSTUDIO_MODEL                    # must match model name loaded in LMStudio

    def chat(self, messages: list[dict], **kwargs) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            **kwargs
        )
        return response.choices[0].message.content
    
# ── OpenRouterAPI ──────────────────────────────────────────────────────────────────
class OpenRouterProvider:
    def __init__(self):
        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=OPENROUTER_API_KEY
        )
        self.model = OPENROUTER_MODEL

    def chat(self, messages: list[dict], **kwargs) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            **kwargs
        )
        return response.choices[0].message.content


# ── OpenAI ────────────────────────────────────────────────────────────────────
class OpenAIProvider:
    def __init__(self):
        self.client = OpenAI(api_key=OPENAI_API_KEY)
        self.model = OPENAI_MODEL                      # e.g. "gpt-4o"

    def chat(self, messages: list[dict], **kwargs) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            **kwargs
        )
        return response.choices[0].message.content




# ── FastAPI (your GPU server) ─────────────────────────────────────────────────
# Your server exposes POST /v1/chat/completions — same format as OpenAI
# So we use the OpenAI client pointing to your server URL
#
# Your server expects:
#   {
#     "model": "rafiaa/terraform-codellama-7b",   <- HuggingFace model name
#     "messages": [...],
#     "temperature": 0.2,
#     "max_tokens": 4096
#   }
#
# Your server returns:
#   {
#     "choices": [{"message": {"role": "assistant", "content": "..."}}]
#   }
#
# Health check: GET /health  → shows GPU memory + cached models
class FastAPIProvider:
    def __init__(self):
        self.client = OpenAI(
            base_url=FASTAPI_BASE_URL,               # e.g. "http://your-gpu-server:8000/v1"
            api_key="not-needed"                     # your server doesn't check this
        )
        self.model = FASTAPI_MODEL                   # HuggingFace model ID e.g. "rafiaa/terraform-codellama-7b"

    def chat(self, messages: list[dict], **kwargs) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            **kwargs
        )
        return response.choices[0].message.content


# ── Gemini ────────────────────────────────────────────────────────────────────
# Gemini does NOT use the OpenAI client — it has its own SDK
# pip install google-generativeai
#
# Gemini expects a flat prompt string OR a list of Content objects
# We convert OpenAI-style messages to Gemini format here
#
# Message format conversion:
#   OpenAI "system" role  → prepended to first user message (Gemini has no system role)
#   OpenAI "user"         → Gemini "user"
#   OpenAI "assistant"    → Gemini "model"
class GeminiProvider:
    def __init__(self):
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        self.model = genai.GenerativeModel(GEMINI_MODEL)   # e.g. "gemini-1.5-flash"

    def _convert_messages(self, messages: list[dict]) -> tuple[str, list]:
        """
        Convert OpenAI messages to Gemini format.
        Returns: (system_prompt_string, gemini_history_list)
        Gemini takes system prompt separately + history as list of dicts
        """
        system_prompt = ""
        history = []

        for msg in messages:
            role = msg["role"]
            content = msg["content"]

            if role == "system":
                system_prompt = content          # Gemini handles system separately

            elif role == "user":
                history.append({
                    "role": "user",
                    "parts": [content]
                })

            elif role == "assistant":
                history.append({
                    "role": "model",             # Gemini uses "model" not "assistant"
                    "parts": [content]
                })

        return system_prompt, history

    def chat(self, messages: list[dict], **kwargs) -> str:
        system_prompt, history = self._convert_messages(messages)

        # Rebuild model with system instruction if present
        if system_prompt:
            model = genai.GenerativeModel(
                GEMINI_MODEL,
                system_instruction=system_prompt
            )
        else:
            model = self.model

        # Last message must be the current user turn
        # Everything before it is history
        if not history:
            return ""

        last_message = history[-1]["parts"][0]
        prior_history = history[:-1]

        chat = model.start_chat(history=prior_history)
        response = chat.send_message(last_message)
        return response.text