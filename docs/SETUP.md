# Setup Completo do Council

Este documento concentra tudo o que é necessário para instalar, configurar e rodar o Council em uma máquina nova, incluindo:

- instalação via `pipx` usando o repositório no GitHub;
- criação e assinatura de `flow.json`;
- explicação de todas as variáveis de ambiente suportadas;
- validações e troubleshooting.

## 1. Pré-requisitos

Antes de instalar o Council, garanta:

- `python` 3.10+ disponível no `PATH`;
- `pipx` instalado;
- providers do seu `flow.json` configurados:
  - CLIs locais (`claude`, `gemini`, `codex`, `ollama`) instaladas/autenticadas no host, quando usadas;
  - providers API (ex: `deepseek`) com variáveis de ambiente necessárias (`DEEPSEEK_API_KEY`).

O fluxo default usa `claude`, `gemini` e `codex`. A allowlist atual também permite `ollama` e `deepseek`.

Exemplo de instalação do `pipx`:

```bash
python3 -m pip install --user pipx
python3 -m pipx ensurepath
```

Depois, reinicie o shell.

## 2. Instalação via pipx (GitHub)

Instalação recomendada (repo público):

```bash
pipx install "git+https://github.com/juniormartinxo/council.git"
```

Alternativa via SSH (quando necessário):

```bash
pipx install "git+ssh://git@github.com/juniormartinxo/council.git"
```

Atualização:

```bash
pipx upgrade council-mas
```

Validação rápida:

```bash
council --help
```

## 3. Bootstrap rápido

1. Crie uma pasta de trabalho (qualquer diretório).
2. Crie ou edite seu fluxo.
3. Rode diagnóstico.
4. Execute.

Comandos mínimos:

```bash
# cria flow.json a partir do template interno (modo simples)
council flow edit flow.json --editor simple

# valida pré-requisitos exigidos pelo fluxo (CLI no PATH ou provider API)
council doctor --flow-config flow.json

# executa fluxo
council run "Seu prompt aqui" --flow-config flow.json
```

Se você preferir TUI:

```bash
council tui --flow-config flow.json
```

## 4. Como criar o flow.json

Você pode criar o fluxo de duas formas:

1. Editor interno:

```bash
council flow edit flow.json --editor tui
# ou
council flow edit flow.json --editor simple
```

2. Copiando um exemplo do repositório (quando você tiver o repo clonado):

```bash
cp flow.example.json flow.json
```

Após criar, ajuste os comandos no campo `command` de cada passo para o provider desejado:
- CLI local disponível no host (ex: `claude -p`, `gemini -p {input}`);
- API provider (ex: `deepseek --model deepseek-chat`).

## 5. Assinatura de flow (keygen/sign/trust/verify)

O Council suporta assinatura Ed25519 com arquivo sidecar `flow.json.sig`.

### 5.1 Gerar par de chaves

```bash
council flow keygen --key-id equipe-seguranca-v1 --trust
```

Isso gera:

- `equipe-seguranca-v1.key.pem` (privada);
- `equipe-seguranca-v1.pub.pem` (pública);
- cópia da pública no trust store local (`--trust`).

### 5.2 Assinar o fluxo

```bash
council flow sign flow.json \
  --private-key equipe-seguranca-v1.key.pem \
  --key-id equipe-seguranca-v1
```

### 5.3 Verificar assinatura

```bash
council flow verify flow.json
```

Se a verificação acontecer em outra máquina, primeiro confie a chave pública:

```bash
council flow trust equipe-seguranca-v1.pub.pem --key-id equipe-seguranca-v1
```

Verificação com chave explícita:

```bash
council flow verify flow.json --public-key equipe-seguranca-v1.pub.pem
```

### 5.4 Exigir assinatura válida em runtime (modo estrito)

```bash
export COUNCIL_REQUIRE_FLOW_SIGNATURE=1
```

Com isso ativo, comandos que carregam fluxo (`run`, `doctor` e execução iniciada pela `tui`) falham se não houver assinatura válida e confiada.

Importante:

- qualquer edição do `flow.json` invalida a assinatura anterior;
- após editar, assine novamente.

## 6. Resolução de flow e comportamento em modo não interativo

Se `--flow-config` não for informado, a ordem de resolução é:

1. `COUNCIL_FLOW_CONFIG`;
2. `./flow.json`;
3. `~/.config/council/flow.json` (ou equivalente por SO);
4. fluxo default interno.

Quando o fluxo vem implicitamente de `COUNCIL_FLOW_CONFIG` ou `./flow.json`, o Council pede confirmação interativa de segurança.

Em modo não interativo (CI/pipeline), essa execução implícita é bloqueada. Use sempre `--flow-config` explicitamente.

## 7. Todas as variáveis de ambiente

### 7.1 Variáveis do Council

| Variável | Default | Valores aceitos | Efeito |
| --- | --- | --- | --- |
| `COUNCIL_FLOW_CONFIG` | vazio | caminho de arquivo existente | Define fluxo padrão sem precisar passar `--flow-config`. |
| `COUNCIL_HOME` | auto por SO | caminho de diretório | Sobrescreve diretório base do Council (estado TUI, logs, trust store, DB, flow global). |
| `COUNCIL_REQUIRE_FLOW_SIGNATURE` | desativado | truthy: `1,true,yes,on`; falsy: `0,false,no,off` (e vazio) | Exige assinatura válida para carregar `flow.json`. Valor inválido falha com erro explícito. |
| `COUNCIL_TRUSTED_FLOW_KEYS_DIR` | `<COUNCIL_HOME>/trusted_flow_keys` | caminho de diretório | Sobrescreve trust store de chaves públicas para verificação de assinatura. |
| `COUNCIL_MAX_CONTEXT_CHARS` | `100000` | inteiro positivo | Limite global de contexto acumulado no estado (`CouncilState`). |
| `COUNCIL_MAX_INPUT_CHARS` | `120000` | inteiro positivo | Limite global de input por execução de comando (`Executor`). |
| `COUNCIL_MAX_OUTPUT_CHARS` | `200000` | inteiro positivo | Limite global de output retido em memória/contexto por comando (`Executor`). |
| `COUNCIL_LOG_LEVEL` | `INFO` | `DEBUG, INFO, WARNING, WARN, ERROR, CRITICAL` | Nível mínimo do log de auditoria. Valor inválido falha na inicialização. |
| `COUNCIL_LOG_MAX_BYTES` | `5242880` | inteiro positivo | Tamanho máximo por arquivo de log antes de rotação. |
| `COUNCIL_LOG_BACKUP_COUNT` | `5` | inteiro positivo | Quantidade de arquivos rotacionados (`.1`, `.2`, ...). |
| `COUNCIL_TUI_STATE_PASSPHRASE` | vazio | string não vazia | Habilita criptografia de histórico de prompts da TUI. Tem precedência sobre arquivo de senha. |
| `COUNCIL_TUI_STATE_PASSPHRASE_FILE` | vazio | caminho de arquivo legível | Fonte alternativa da senha da TUI. Usada quando `COUNCIL_TUI_STATE_PASSPHRASE` não está definido. |
| `DEEPSEEK_API_KEY` | vazio | token válido da DeepSeek API | Obrigatória para executar passos com `command` iniciado por `deepseek`. |
| `DEEPSEEK_API_BASE_URL` | `https://api.deepseek.com` | URL base HTTP(S) | Opcional para sobrescrever endpoint da API DeepSeek. |

### 7.2 Variáveis de ambiente do SO que influenciam caminhos

| Variável | Onde impacta |
| --- | --- |
| `XDG_CONFIG_HOME` | Base para `COUNCIL_HOME` default em Linux. |
| `APPDATA` | Base para `COUNCIL_HOME` default em Windows. |

## 8. Arquivos e diretórios criados pelo Council

- `COUNCIL_HOME/tui_state.json`: estado da TUI e histórico de prompts.
- `COUNCIL_HOME/council.log`: log de auditoria estruturado.
- `COUNCIL_HOME/db/history.sqlite3`: histórico de runs e passos.
- `COUNCIL_HOME/trusted_flow_keys/*.pem`: chaves públicas confiadas.
- `flow.json.sig`: assinatura sidecar do flow.

## 9. Exemplo de perfil local (.envrc.local)

Se usar `direnv`, você pode manter configurações por máquina em `.envrc.local`:

```bash
export COUNCIL_REQUIRE_FLOW_SIGNATURE=1
export COUNCIL_FLOW_CONFIG="$PWD/flow.json"
export COUNCIL_HOME="$PWD/.council-home"
```

## 10. Troubleshooting rápido

- `Configuração inválida de logging`: revise `COUNCIL_LOG_LEVEL`, `COUNCIL_LOG_MAX_BYTES`, `COUNCIL_LOG_BACKUP_COUNT`.
- `Configuração inválida de limites`: revise `COUNCIL_MAX_CONTEXT_CHARS`, `COUNCIL_MAX_INPUT_CHARS`, `COUNCIL_MAX_OUTPUT_CHARS`.
- `Pré-requisitos ausentes`: rode `council doctor --flow-config flow.json`; para CLIs faltantes, instale binários no `PATH`; para `deepseek`, valide `DEEPSEEK_API_KEY`.
- `Execução bloqueada em modo não interativo`: passe `--flow-config` explicitamente.
- `Falha na verificação da assinatura`: valide `flow.json.sig`, `key_id` e presença da chave pública no trust store.

## 11. Checklist final de go-live

1. `council --help` funciona.
2. `council doctor --flow-config flow.json` sem erros.
3. `flow.json` assinado e verificado (`council flow verify flow.json`).
4. `COUNCIL_REQUIRE_FLOW_SIGNATURE=1` ativado (recomendado para produção).
5. `council run "prompt de teste" --flow-config flow.json` concluído.
