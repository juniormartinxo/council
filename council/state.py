from dataclasses import dataclass, field
from typing import List

from council.limits import read_positive_int_env


DEFAULT_MAX_CONTEXT_CHARS = 100_000
MAX_CONTEXT_CHARS_ENV_VAR = "COUNCIL_MAX_CONTEXT_CHARS"
CONTEXT_TRUNCATION_NOTICE = "[... contexto truncado ...]\n"

def _truncate_with_notice(content: str, limit: int) -> str:
    if len(content) <= limit:
        return content
    if limit <= len(CONTEXT_TRUNCATION_NOTICE):
        return CONTEXT_TRUNCATION_NOTICE[:limit]
    tail_size = limit - len(CONTEXT_TRUNCATION_NOTICE)
    return f"{CONTEXT_TRUNCATION_NOTICE}{content[-tail_size:]}"

@dataclass
class Turn:
    agent: str
    role: str
    content: str
    action: str = ""

@dataclass
class CouncilState:
    """Gerenciador de estado para manter o histórico da conversa entre chamadas."""
    history: List[Turn] = field(default_factory=list)
    max_context_chars: int = field(
        default_factory=lambda: read_positive_int_env(
            MAX_CONTEXT_CHARS_ENV_VAR,
            DEFAULT_MAX_CONTEXT_CHARS,
        )
    )

    def __post_init__(self) -> None:
        if self.max_context_chars <= 0:
            raise ValueError("max_context_chars deve ser um inteiro positivo.")

    def add_turn(self, agent: str, role: str, content: str, action: str = ""):
        self.history.append(Turn(agent, role, content, action))

    def get_full_context(self, max_chars: int | None = None) -> str:
        """
        Gera uma representação formatada do histórico da conversa.
        """
        effective_limit = self.max_context_chars if max_chars is None else max_chars
        if effective_limit <= 0:
            raise ValueError("max_chars deve ser um inteiro positivo.")

        if not self.history:
            return ""
            
        context_parts = []
        for turn in self.history:
            header = f"\n--- {turn.agent} ({turn.role.upper()}) ---"
            if turn.action:
                header += f" [Ação: {turn.action}]"
            context_parts.append(header)
            context_parts.append(turn.content.strip())
            
        full_context = "\n".join(context_parts).strip()
        return _truncate_with_notice(full_context, effective_limit)
