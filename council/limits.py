import os


def read_positive_int_env(env_var: str, default: int) -> int:
    """Lê inteiro positivo de variável de ambiente.

    Regras:
    - variável ausente/vazia => default
    - valor inválido (não inteiro ou <= 0) => ValueError
    """
    raw_value = os.getenv(env_var, "").strip()
    if not raw_value:
        return default

    try:
        parsed = int(raw_value)
    except ValueError as exc:
        raise ValueError(
            f"Variável de ambiente '{env_var}' inválida: esperado inteiro positivo, recebido '{raw_value}'."
        ) from exc

    if parsed <= 0:
        raise ValueError(
            f"Variável de ambiente '{env_var}' inválida: esperado inteiro positivo, recebido '{raw_value}'."
        )

    return parsed
