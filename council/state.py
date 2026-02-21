from dataclasses import dataclass, field
from typing import List

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

    def add_turn(self, agent: str, role: str, content: str, action: str = ""):
        self.history.append(Turn(agent, role, content, action))

    def get_full_context(self) -> str:
        """
        Gera uma representação formatada do histórico da conversa.
        """
        if not self.history:
            return ""
            
        context_parts = []
        for turn in self.history:
            header = f"\n--- {turn.agent} ({turn.role.upper()}) ---"
            if turn.action:
                header += f" [Ação: {turn.action}]"
            context_parts.append(header)
            context_parts.append(turn.content.strip())
            
        return "\n".join(context_parts).strip()
