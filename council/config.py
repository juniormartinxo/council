import json
import os
import re
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path
from string import Formatter
from typing import Any, Literal, Mapping

from council.paths import get_user_flow_config_path
from council.flow_signature import (
    FlowSignatureError,
    parse_signature_required_from_env,
    verify_flow_signature,
)


FLOW_CONFIG_ENV_VAR = "COUNCIL_FLOW_CONFIG"
FLOW_CONFIG_SOURCE_CLI = "cli"
FLOW_CONFIG_SOURCE_ENV = "env"
FLOW_CONFIG_SOURCE_CWD = "cwd"
FLOW_CONFIG_SOURCE_USER = "user"
FLOW_CONFIG_SOURCE_DEFAULT = "default"
FlowConfigSource = Literal["cli", "env", "cwd", "user", "default"]
RESERVED_TEMPLATE_KEYS = {"user_prompt", "full_context", "last_output", "instruction"}
DISALLOWED_COMMAND_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\n"), "\\n"),
    (re.compile(r"\r"), "\\r"),
    (re.compile(r"&&"), "&&"),
    (re.compile(r";"), ";"),
    (re.compile(r"\|"), "|"),
    (re.compile(r"`"), "`"),
    (re.compile(r"\$\{"), "${"),
    (re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*"), "$VAR"),
    (re.compile(r"\$\("), "$("),
    (re.compile(r"(^|\s)~(?=/|$)"), "~"),
    (re.compile(r">>"), ">>"),
    (re.compile(r"(?<!>)>(?!>)"), ">"),
)
ALLOWED_COMMAND_BINARIES = frozenset({"claude", "gemini", "codex", "ollama", "deepseek"})
API_ONLY_COMMAND_BINARIES = frozenset({"deepseek"})
_TEMPLATE_FORMATTER = Formatter()
_TEMPLATE_FIELD_BASE_PATTERN = re.compile(r"[.\[]")


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
    enabled: bool = True
    timeout: int = 120
    max_input_chars: int | None = None
    max_output_chars: int | None = None
    max_context_chars: int | None = None


@dataclass(frozen=True)
class ResolvedFlowConfig:
    path: Path | None
    source: FlowConfigSource


def get_default_flow_steps() -> list[FlowStep]:
    plan_instruction = (
        "Você é um arquiteto de software sênior, pragmático e orientado a entregas. "
        "Analise o requisito abaixo e produza um plano de implementação estruturado contendo:\n\n"
        "1. VISÃO GERAL — Resumo do que será feito e por quê.\n"
        "2. ARQUITETURA — Componentes envolvidos, dependências e integrações.\n"
        "3. PASSOS DE IMPLEMENTAÇÃO — Lista ordenada e detalhada das tarefas, "
        "com arquivos a criar/modificar.\n"
        "4. RISCOS E MITIGAÇÕES — Problemas potenciais e como evitá-los.\n"
        "5. CRITÉRIOS DE SUCESSO — Como validar que a implementação está correta.\n\n"
        "Seja específico com nomes de arquivos, funções e estruturas de dados. "
        "Evite generalidades."
    )
    critique_instruction = (
        "Você é um auditor técnico rigoroso e cético. "
        "Sua função é encontrar falhas que o arquiteto não viu. "
        "Analise o plano com foco em:\n\n"
        "1. FALHAS DE ARQUITETURA — Acoplamento excessivo, violações de SOLID, escalabilidade.\n"
        "2. SEGURANÇA — Injeção, exposição de dados, permissões inadequadas, supply chain.\n"
        "3. EDGE CASES — Cenários não cobertos, condições de corrida, falhas de rede/IO.\n"
        "4. COMPLEXIDADE DESNECESSÁRIA — Onde o plano poderia ser simplificado sem perder qualidade.\n"
        "5. DEPENDÊNCIAS E RISCOS — Bibliotecas desatualizadas, lock-in, compatibilidade.\n\n"
        "Para cada problema encontrado, classifique a severidade como "
        "[CRÍTICO], [ALTO], [MÉDIO] ou [BAIXO] e sugira uma correção concreta. "
        "Não elogie o que está bom — foque apenas nos problemas."
    )
    consolidation_instruction = (
        "Você é o arquiteto decisor final deste projeto. "
        "Você recebeu um plano inicial e críticas de um auditor técnico. Sua tarefa:\n\n"
        "1. Analise cada crítica objetivamente.\n"
        "2. ACEITE as críticas que são válidas e ajuste o plano de implementação de acordo.\n"
        "3. REJEITE as críticas que considerar improcedentes, justificando brevemente por quê.\n"
        "4. Produza o PLANO FINAL CONSOLIDADO — este será o documento que "
        "o implementador seguirá ao pé da letra.\n\n"
        "Formato da saída:\n"
        "- DECISÕES SOBRE CRÍTICAS — Lista de críticas aceitas/rejeitadas com justificativa.\n"
        "- PLANO FINAL — O plano completo e atualizado, pronto para implementação, "
        "com os mesmos 5 pontos do plano original "
        "(Visão Geral, Arquitetura, Passos, Riscos, Critérios)."
    )
    implementation_instruction = (
        "Você é um engenheiro de software sênior focado em implementação limpa e produtiva. "
        "Implemente EXATAMENTE o que o plano consolidado especifica. Regras:\n\n"
        "- Retorne APENAS código-fonte. Sem explicações, sem comentários desnecessários.\n"
        "- Siga as convenções do projeto existente (linguagem, estilo, estrutura de diretórios).\n"
        "- Inclua tratamento de erros e validação de entrada onde aplicável.\n"
        "- Se o plano especificar testes, implemente-os também."
    )
    review_instruction = (
        "Você é um revisor de código especializado em segurança e robustez. "
        "Compare o código implementado com o plano consolidado e avalie:\n\n"
        "1. CONFORMIDADE — O código implementa fielmente o que o plano especifica?\n"
        "2. SEGURANÇA — Há vulnerabilidades "
        "(injeção, XSS, SSRF, path traversal, secrets expostos)?\n"
        "3. BUGS — Erros lógicos, off-by-one, null references, condições de corrida?\n"
        "4. TESTES — A cobertura de testes é adequada? Faltam casos de borda?\n\n"
        "Classifique cada achado como [CRÍTICO], [ALTO], [MÉDIO] ou [BAIXO]. "
        "Seja direto e objetivo."
    )

    return [
        FlowStep(
            key="plan",
            agent_name="Claude",
            role_desc="Planejamento",
            command="claude -p",
            instruction=plan_instruction,
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
                "{instruction}\n\nPLANO INICIAL:\n{plan}\n\n"
                "CRÍTICAS RECEBIDAS:\n{critique}"
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
            input_template="{instruction}\n\nPLANO CONSOLIDADO:\n{final_plan}\n\nCÓDIGO:\n{code}",
            style="dodger_blue1",
        ),
    ]


def load_flow_steps(
    config_path: str | None,
    resolved_config: ResolvedFlowConfig | None = None,
) -> list[FlowStep]:
    selected_config = resolved_config or resolve_flow_config(config_path)
    resolved_path = selected_config.path
    if resolved_path is None:
        return get_default_flow_steps()

    try:
        serialized_payload_bytes = resolved_path.read_bytes()
    except OSError as exc:
        raise ConfigError(f"Falha ao ler configuração de fluxo em '{resolved_path}': {exc}") from exc

    try:
        require_signature = parse_signature_required_from_env()
        verify_flow_signature(
            resolved_path,
            require_signature=require_signature,
            flow_content=serialized_payload_bytes,
        )
    except FlowSignatureError as exc:
        raise ConfigError(f"Falha na verificação de assinatura em '{resolved_path}': {exc}") from exc

    try:
        serialized_payload = serialized_payload_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigError(
            f"Conteúdo inválido em '{resolved_path}': esperado JSON em UTF-8."
        ) from exc

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

    validate_flow_template_references(steps)

    return steps


def resolve_flow_config(config_path: str | None) -> ResolvedFlowConfig:
    cli_path = (config_path or "").strip()
    if cli_path:
        validated_path = _validate_config_path(cli_path, source="--flow-config")
        return ResolvedFlowConfig(path=validated_path, source=FLOW_CONFIG_SOURCE_CLI)

    env_path = os.getenv(FLOW_CONFIG_ENV_VAR, "").strip()
    if env_path:
        validated_path = _validate_config_path(env_path, source=FLOW_CONFIG_ENV_VAR)
        return ResolvedFlowConfig(path=validated_path, source=FLOW_CONFIG_SOURCE_ENV)

    cwd_path = Path.cwd() / "flow.json"
    if cwd_path.exists():
        return ResolvedFlowConfig(path=cwd_path, source=FLOW_CONFIG_SOURCE_CWD)

    user_path = get_user_flow_config_path()
    if user_path.exists():
        return ResolvedFlowConfig(path=user_path, source=FLOW_CONFIG_SOURCE_USER)

    return ResolvedFlowConfig(path=None, source=FLOW_CONFIG_SOURCE_DEFAULT)


def _resolve_flow_config_path(config_path: str | None) -> Path | None:
    return resolve_flow_config(config_path).path


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


def _extract_template_variables(input_template: str) -> set[str]:
    variables: set[str] = set()
    for _literal, field_name, _format_spec, _conversion in _TEMPLATE_FORMATTER.parse(input_template):
        if field_name is None:
            continue
        cleaned_field_name = field_name.strip()
        if not cleaned_field_name:
            continue
        base_name = _TEMPLATE_FIELD_BASE_PATTERN.split(cleaned_field_name, maxsplit=1)[0].strip()
        if base_name:
            variables.add(base_name)
    return variables


def validate_flow_template_references(steps: list[FlowStep]) -> None:
    available_variables = set(RESERVED_TEMPLATE_KEYS)
    for step in steps:
        referenced_variables = _extract_template_variables(step.input_template)
        missing_variables = sorted(
            variable for variable in referenced_variables if variable not in available_variables
        )
        if missing_variables:
            missing_key = missing_variables[0]
            raise ConfigError(
                f"O passo '{step.key}' referencia a variável inexistente '{missing_key}' no input_template."
            )
        available_variables.add(step.key)


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
    enabled = _get_bool(raw_step, "enabled", default=True)
    timeout = _get_optional_positive_int(raw_step, "timeout", step=position) or 120
    max_input_chars = _get_optional_positive_int(raw_step, "max_input_chars", step=position)
    max_output_chars = _get_optional_positive_int(raw_step, "max_output_chars", step=position)
    max_context_chars = _get_optional_positive_int(raw_step, "max_context_chars", step=position)

    return FlowStep(
        key=key,
        agent_name=agent_name,
        role_desc=role_desc,
        command=command,
        instruction=instruction,
        input_template=input_template,
        style=style,
        is_code=is_code,
        enabled=enabled,
        timeout=timeout,
        max_input_chars=max_input_chars,
        max_output_chars=max_output_chars,
        max_context_chars=max_context_chars,
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


def _get_optional_positive_int(source: dict[str, Any], field_name: str, step: int) -> int | None:
    if field_name not in source:
        return None

    value = source[field_name]
    if not isinstance(value, int) or isinstance(value, bool):
        raise ConfigError(f"O campo '{field_name}' no passo #{step} deve ser inteiro positivo.")

    if value <= 0:
        raise ConfigError(f"O campo '{field_name}' no passo #{step} deve ser maior que zero.")

    return value


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
    if "/" in binary or "\\" in binary:
        raise ConfigError(
            f"O campo 'command' no passo #{step} deve usar apenas o nome do binário, sem caminho explícito: '{binary}'."
        )

    if binary in API_ONLY_COMMAND_BINARIES:
        return

    if shutil.which(binary) is None:
        raise ConfigError(
            f"O campo 'command' no passo #{step} usa binário inexistente no PATH: '{binary}'."
        )

    if binary not in ALLOWED_COMMAND_BINARIES:
        allowed_bins_text = ", ".join(sorted(ALLOWED_COMMAND_BINARIES))
        raise ConfigError(
            f"O campo 'command' no passo #{step} usa binário não permitido: '{binary}'. "
            f"Permitidos: {allowed_bins_text}."
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
