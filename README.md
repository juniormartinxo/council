<h1 align="center">Council 🏛️ <br/> <em>AI Multi-Agent Orquestrator (MAS)</em></h1>

<p align="center">
  <strong>Uma arquitetura robusta, assíncrona e orientada a eventos para orquestração de Modelos Fundacionais Locais e em Nuvem via CLI.</strong>
</p>

---

## 🚀 Sobre o Projeto

O **Council** é um orquestrador CLI construído do zero em **Python**, que projeta um consenso automatizado (Multi-Agent System) entre instâncias distintas de LLMs. Em vez de depender de pesadas bibliotecas de abstração de IA (como LangChain ou AutoGen), o Council adota uma abordagem de infraestrutura *agnóstica*, conectando-se diretamente a ferramentas bash e CLIs independentes (`claude`, `gemini`, `codex`) via injeção segura de `stdin/stdout`.

Este projeto é um laboratório prático de **Engenharia de Software e Arquitetura de Sistemas**, demonstrando forte domínio em gerenciamento de processos do Sistema Operacional, manipulação de streams de dados IO sem bloqueio, e desenvolvimento de interfaces ricas baseadas em terminal (TUI).

## 🧠 Soluções de Engenharia e Arquitetura

O desenvolvimento do Council focou-se na resiliência e na separação de responsabilidades (SoC), abordando os seguintes desafios técnicos complexos:

### 1. Manipulação Assíncrona de Subprocessos
Chamadas a LLMs são bloqueantes por natureza. Pípes padrão (como `subprocess.communicate()`) fariam o programa refém do tempo de geração do modelo, ofuscando e limitando o feedback visual do terminal.
* **A Solução:** Implementou-se uma leitura em tempo real (linha por linha, sem *buffer*) do descritor de arquivo `stdout` da ferramenta externa. Utilizando iteradores de leitura passados a callbacks injetados, construiu-se uma ponte limpa entre o modelo rodando no kernel (filho) e a interface do usuário (pai), permitindo exibição do `Live Stream` na tela milissegundos após o token ter sido retornado pelo LLM. 

### 2. Gerenciamento de Estado de Contexto Contínuo
Como CLIs são *stateless*, cada chamada a um agente esquece a iteração isolada do agente anterior.
* **A Solução:** Uma classe de domínio `CouncilState` gerencia a Memória da aplicação, encapsulando coleções de *Turns* e injetando na estrutura das prompts dinamicamente os cabeçalhos de papel e as respostas consolidadas de execuções anteriores no subshell, forçando a preservação do escopo do pipeline.

### 3. Integração em Ambiente Headless e Tratamento de TTY
CLIs complexos detectam a presença do shell `tty` nativo. Invocações programáticas causam falhas como "stdin is not a terminal" se manuseadas incorretamente, além de interrupções por agentes tentando pedir validação do humano na tela.
* **A Solução:** Engenharia reversa para envio de metadados invisíveis/parametrizadores (ex: flags `-p`, ou subcomandos headless como `exec`) isolando e castrando os módulos gráficos ou de aprovação (*Yolo Mode* programático), forçando os *clients* a interpretarem o programa Python em canais canônicos absolutos de texto limpo. Fechamento proativo de buffers (`stdin.close()`) forçando envio EOF para evitar pipelines corrompidos (Dangling processes).

### 4. Event-Driven UI com Context Managers Modernos
A biblioteca `rich` e o `typer` compõem a porta de entrada.
* **A Solução:** Emprego extensivo de `@contextmanager` para isolar fluxos UI. Um Painel Dinâmico é capaz de renderizar as últimas `N` linhas emitidas de um LLM como tela de log e se auto-destruir de forma limpa (`transient=True`), sendo trocado perfeitamente pelo Syntax Highlighter de `markdown` para a versão imutável do log validado. Tudo através de injeção de dependência rudimentar do injetor raiz (`Orchestrator(state, executor, ui)`).

---

## 🛠️ Stack Tecnológica

| Tecnologia | Função no Projeto |
| :--- | :--- |
| **Python 3.10+** | Core languange focado em Type Hinting modernos (`typing_extensions.Annotated`). |
| **Typer** | Roteamento nativo e performático de argumentos via Python types. |
| **Rich** | Controle de Buffer de frame do Terminal (Painéis, Syntax Highlighting, Spinners, Live Updates). |
| **Subprocess** | Integração em baixo nível de Pipes SO Popen (`stdin`, `stdout`, `stderr`). |
| **OOP / SOLID** | Padrões de classes dedicados a Responsabilidade Única (UI, Estado, Execução Pura). |

## 🧬 Dissecando o Loop de Consenso

O Orchestrator executa a seguinte topologia seqüencial em pipeline para processamento da entrada:

1. `Claude` **[Arquitetura]**: Planeja os diagramas lógicos a partir do input primitivo.
2. `Gemini` **[Critique]**: Audita as fragilidades, segurança falha e complexidades excessivas (Big-O).
3. `Claude` **[Consolidation]**: Refatora as fraquezas sistêmicas do design original.
4. `Codex` **[Engineer]**: Converte a macro visão consolidada em código-fonte direto ao ponto.
5. `Gemini` **[Reviewer]**: Inspeciona falhas sintáticas ou de coesão, fechando o loop. 

## ⚙️ Instalação Local

O ambiente não exige o uso do LangChain. Tudo se resume a ferramentas CLI padronizadas que existam no PATH do repositório/SO rodando de fato a mágica.

```bash
# Geração do ambiente virtual restrito e ativado
python3 -m venv venv
source venv/bin/activate

# Instalação das dependências (Rich, Typer etc)
pip install -r requirements.txt

# Dispara a orquestração enviando o STDIN global para os sub-nós
python -m council.main run "Crie um algoritmo distribuido de map-reduce"
```

---
*Construído com base em design system limpo de código e arquitetura adaptável.*
