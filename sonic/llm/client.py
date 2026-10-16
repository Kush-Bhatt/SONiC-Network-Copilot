from sonic.config.settings import DEFAULT_LLM_PROVIDER
from sonic.llm.provider import OpenAIProvider, LMStudioProvider, GeminiProvider, FastAPIProvider, OpenRouterProvider



PROVIDERS = {
    "openai": OpenAIProvider,
    "gemini": GeminiProvider,
    "lmstudio": LMStudioProvider,
    "fastapi": FastAPIProvider,
    "openrouter":  OpenRouterProvider,
}

def get_llm_client(provider: str = None):
    provider = provider or DEFAULT_LLM_PROVIDER
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown provider: {provider}. Choose from {list(PROVIDERS.keys())}")
    return PROVIDERS[provider]()