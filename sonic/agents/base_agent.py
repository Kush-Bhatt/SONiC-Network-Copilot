"""
inherited by all agents

now : system prompt + user quer , chat() 
future : memory, RAG, docs, error, online  search
"""

from sonic.llm.client import get_llm_client


class BaseAgent:
    """
    Minimal base agent.
    Takes system prompt + user query → returns clean LLM response string.
    
    Future improvements (RAG, memory, docs) go here only.
    Workflow code never needs to change.
    """

    def __init__(self, system_prompt: str, provider: str = None):
        self.system_prompt = system_prompt
        self.llm = get_llm_client(provider)

    def chat(self, query: str) -> str:
        messages = [
            {"role": "developer", "content": self.system_prompt},
            {"role": "user",   "content": query}
        ]
        response = self.llm.chat(messages)
        return self._clean(response)

    def _clean(self, text: str) -> str:
        """Strip markdown fences and whitespace from LLM response."""
        text = text.strip()
        if text.startswith("```"):
            parts = text.split("```")
            if len(parts) >= 2:
                text = parts[1]
            if text.startswith("yaml") or text.startswith("python"):
                text = text.split("\n", 1)[-1]
        return text.strip()