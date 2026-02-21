import json
import os
import re
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from council.paths import get_user_flow_config_path


FLOW_CONFIG_ENV_VAR = "COUNCIL_FLOW_CONFIG"
RESERVED_TEMPLATE_KEYS = {"user_prompt", "full_context", "last_output", "instruction"}
DISALLOWED_COMMAND_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\n"), "\\n"),
    (re.compile(r"\r"), "\\r"),
    (re.compile(r"&&"), "&&"),
    (re.compile(r";"), ";"),
    (re.compile(r"\|"), "|"),
    (re.compile(r"`"), "`"),
    (re.compile(r"\$\("), "$("),
    (re.compile(r">>"), ">>"),
    (re.compile(r"(?<!>)>(?!>)"), ">"),
)


class ConfigError(Exception):
    """Erro de configuração do fluxo de agentes."""


@dataclass(frozen=True)
class FlowStep:
    key: str
    agent_name: str
    role_desc: str
    command: str
    instruction: str
    input_template: str = "{instruction}\n\n{full_context}"
    style: str = "blue"
    is_code: bool = False


def get_default_flow_steps() -> list[FlowStep]:
    critique_instruction = (
        "Analise o seguinte plano de arquitetura. "
        "Aponte falhas de arquitetura e possíveis problemas de segurança:"
    )
    consolidation_instruction = (
        "O plano inicial recebeu as seguintes críticas. "
        "Consolide, resolva os problemas e gere o plano final corrigido:"
    )
    implementation_instruction = (
        "Você é um engenheiro de software sênior. Implemente o código conforme o seguinte plano. "
        "RETORNE APENAS O CÓDIGO FONTE FINAL E MAIS NADA, sem explicações em texto."
    )
    review_instruction = (
        "Você é um revisor de código rigoroso. Faça um code review detalhado do código a seguir, "
        "apontando boas práticas, bugs ocultos, problemas de segurança ou pontos de melhoria:"
    )

    return [
        FlowStep(
            key="plan",
            agent_name="Claude",
            role_desc="Planejamento",
            command="claude -p",
            instruction="Você é um arquiteto de software. Crie um plano detalhado para o seguinte requisito:",
            input_template="{instruction}\n\nCONTEXTO:\n{full_context}",
            style="dark_goldenrod",
        ),
        FlowStep(
            key="critique",
            agent_name="Gemini",
            role_desc="Crítica",
            command="gemini -p {input}",
            instruction=critique_instruction,
            input_template="{instruction}\n\nPLANO PROPOSTO:\n{plan}",
            style="dodger_blue1",
        ),
        FlowStep(
            key="final_plan",
            agent_name="Claude",
            role_desc="Consolidação",
            command="claude -p",
            instruction=consolidation_instruction,
            input_template=(
                "PLANO INICIAL:\n{plan}\n\nCRÍTICAS RECEBIDAS:\n{critique}\n\n{instruction}"
            ),
            style="dark_goldenrod",
        ),
        FlowStep(
            key="code",
            agent_name="Codex",
            role_desc="Implementação",
            command="codex exec --skip-git-repo-check",
            instruction=implementation_instruction,
            input_template="{instruction}\n\nPLANO FINAL:\n{final_plan}",
            style="bright_black",
            is_code=True,
        ),
        FlowStep(
            key="review",
            agent_name="Gemini",
            role_desc="Revisão Final",
            command="gemini -p {input}",
            instruction=review_instruction,
            input_template="{instruction}\n\nCÓDIGO:\n{code}",
            style="dodger_blue1",
        ),
    ]


def load_flow_steps(config_path: str | None) -> list[FlowStep]:
    resolved_path = _resolve_flow_config_path(config_path)
    if resolved_path is None:
        return get_default_flow_steps()

    try:
        serialized_payload = resolved_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"Falha ao ler configuração de fluxo em '{resolved_path}': {exc}") from exc

    try:
        payload = json.loads(serialized_payload)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"JSON inválido em '{resolved_path}': {exc.msg}") from exc

    raw_steps = _extract_steps(payload)
    steps = [_parse_step(raw_step, index + 1) for index, raw_step in enumerate(raw_steps)]

    if not steps:
        raise ConfigError("A configuração precisa conter pelo menos 1 passo.")

    duplicate_keys = _find_duplicate_keys([step.key for step in steps])
    if duplicate_keys:
        duplicates_as_text = ", ".join(sorted(duplicate_keys))
        raise ConfigError(f"Chaves de passos duplicadas: {duplicates_as_text}")

    reserved_conflicts = sorted(step.key for step in steps if step.key in RESERVED_TEMPLATE_KEYS)
    if reserved_conflicts:
        conflicts_as_text = ", ".join(reserved_conflicts)
        raise ConfigError(
            f"As chaves de passos não podem usar nomes reservados ({conflicts_as_text})."
        )

    return steps


def _resolve_flow_config_path(config_path: str | None) -> Path | None:
    cli_path = (config_path or "").strip()
    if cli_path:
        return _validate_config_path(cli_path, source="--flow-config")

    env_path = os.getenv(FLOW_CONFIG_ENV_VAR, "").strip()
    if env_path:
        return _validate_config_path(env_path, source=FLOW_CONFIG_ENV_VAR)

    cwd_path = Path.cwd() / "flow.json"
    if cwd_path.exists():
        return cwd_path

    user_path = get_user_flow_config_path()
    if user_path.exists():
        return user_path

    return None


def _validate_config_path(raw_path: str, source: str) -> Path:
    path = Path(raw_path).expanduser()
    if not path.exists():
        raise ConfigError(f"Arquivo de configuração não encontrado ({source}): {path}")
    if not path.is_file():
        raise ConfigError(f"O caminho informado ({source}) não é um arquivo: {path}")
    return path


def render_step_input(step: FlowStep, context: Mapping[str, str]) -> str:
    try:
        return step.input_template.format_map(context)
    except KeyError as exc:
        missing_key = exc.args[0]
        raise ConfigError(
            f"O passo '{step.key}' referencia a variável inexistente '{missing_key}' no input_template."
        ) from exc


def _extract_steps(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return payload

    if isinstance(payload, dict) and isinstance(payload.get("steps"), list):
        return payload["steps"]

    raise ConfigError(
        "Formato inválido. Use uma lista de passos ou um objeto JSON com a chave 'steps'."
    )


def _parse_step(raw_step: Any, position: int) -> FlowStep:
    if not isinstance(raw_step, dict):
        raise ConfigError(f"Passo #{position} inválido: esperado objeto JSON.")

    key = _get_string(raw_step, ["key", "id"], required=False) or f"step_{position}"
    agent_name = _get_string(raw_step, ["agent_name", "agent"], required=True, step=position)
    role_desc = _get_string(raw_step, ["role_desc", "role"], required=True, step=position)
    command = _get_string(raw_step, ["command"], required=True, step=position)
    _validate_command(command, step=position)
    instruction = _get_string(raw_step, ["instruction"], required=True, step=position)
    input_template = (
        _get_string(raw_step, ["input_template"], required=False)
        or "{instruction}\n\n{full_context}"
    )
    style = _get_string(raw_step, ["style"], required=False) or "blue"
    is_code = _get_bool(raw_step, "is_code", default=False)

    return FlowStep(
        key=key,
        agent_name=agent_name,
        role_desc=role_desc,
        command=command,
        instruction=instruction,
        input_template=input_template,
        style=style,
        is_code=is_code,
    )


def _get_string(
    source: dict[str, Any],
    field_names: list[str],
    required: bool,
    step: int | None = None,
) -> str | None:
    for name in field_names:
        if name not in source:
            continue

        value = source[name]
        if not isinstance(value, str):
            location = f" no passo #{step}" if step is not None else ""
            raise ConfigError(f"O campo '{name}'{location} deve ser string.")

        cleaned = value.strip()
        if not cleaned and required:
            location = f" no passo #{step}" if step is not None else ""
            raise ConfigError(f"O campo '{name}'{location} não pode ser vazio.")

        return cleaned

    if required:
        location = f" no passo #{step}" if step is not None else ""
        expected = " ou ".join(f"'{name}'" for name in field_names)
        raise ConfigError(f"Campo obrigatório ausente ({expected}){location}.")

    return None


def _validate_command(command: str, step: int) -> None:
    disallowed_operators = [
        label for pattern, label in DISALLOWED_COMMAND_PATTERNS if pattern.search(command)
    ]
    if disallowed_operators:
        operators_as_text = ", ".join(disallowed_operators)
        raise ConfigError(
            f"O campo 'command' no passo #{step} contém operadores de shell não permitidos ({operators_as_text})."
        )

    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        raise ConfigError(
            f"O campo 'command' no passo #{step} possui sintaxe inválida: {exc}."
        ) from exc

    if not tokens:
        raise ConfigError(f"O campo 'command' no passo #{step} não pode ser vazio.")

    binary = tokens[0]
    if shutil.which(binary) is None:
        raise ConfigError(
            f"O campo 'command' no passo #{step} usa binário inexistente no PATH: '{binary}'."
        )


def _get_bool(source: dict[str, Any], field_name: str, default: bool) -> bool:
    if field_name not in source:
        return default

    value = source[field_name]
    if isinstance(value, bool):
        return value

    raise ConfigError(f"O campo '{field_name}' deve ser booleano.")


def _find_duplicate_keys(keys: list[str]) -> set[str]:
    duplicates = set()
    seen = set()

    for key in keys:
        if key in seen:
            duplicates.add(key)
        seen.add(key)

    return duplicates
