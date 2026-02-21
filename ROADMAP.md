# Roadmap de Produto: Council (Terminal-First)

A essência e o grande diferencial do **Council** é ser uma ferramenta "Direct-to-Developer" focada no isolamento que apenas o terminal e chamadas de Unix/Pipes proporcionam.

Para transformá-lo em um produto premium para desenvolvedores (uma "DevTool" distribuível via `brew`, `apt`, `npm` ou binário standalone), o objetivo é elevar a experiência do terminal ao máximo, mantendo-o **100% no Terminal e CLI**.

Abaixo estão os pilares de evolução para produtizar o Council:

## 1. Sistema Dinâmico de Grafos e Loops Condicionais (Orquestração Avançada)

Atualmente o `flow.json` é sequencial (`step a` -> `step b`). Em um produto maduro, o desenvolvedor precisa lidar com falhas arquiteturais ou testes que não passam diretamente na CLI.
*   **Condicionais e Desvios:** Permitir diretivas no JSON baseadas em regras de fallback. Ex: `condition: "if error in {review} goto {implement}"`. 
*   **Aprovação Automatizada (Auto-Evaluate):** Introduzir a figura de um executor de scripts/verificador no pipeline. Exemplo: um agente gera código Python, um passo intermediário executa `pytest`, e se falhar (exit code != 0), o Output volta para o `Codex` consertar o próprio código automaticamente até passar ou atingir o limite de loops (`max_loops: 3`).

## 2. Ambientes de Sandboxing Seguros (Ferramentas no Terminal)

Se os agentes interagirem entre si e precisarem listar diretórios, criar arquivos massivos ou testar comandos do sistema fora da aprovação da TUI, deixá-los atuar diretamente sobre o host do usuário é um grande risco de segurança e arquitetura.
*   **Integração nativa com Docker:** Uma feature `council run --isolated`, onde o pipeline criaria um container efêmero invisível, injetaria os binários e STDINs ali dentro, e só devolveria os resultados (`code`, `diffs`) finais validados. O usuário aprovaria apenas o *Merge* das alterações no host.

## 3. Experiência e Instalação (Distribuição Independente)

Um produto precisa ser instalável de forma universal sem fricção, evitando dores de cabeça com ambientes Virtuais Python (venvs) ou gerenciamento de bibliotecas por fora.
*   **Binário Autocontido:** Compilar o Council usando PyInstaller, Nuitka, ou empacotador similar para gerar binários únicos (`council-linux-x64`, `council-macos-arm64`). O cliente faria apenas: `curl -fsSL https://council.dev/install | bash`.
*   **Gerenciador de Dependências de Modelos Embutido:** Hoje, o Council depende puramente da injeção no PATH via CLI (ex: chamadas `claude` e `gemini`). Para um onboarding de produto mais suave, introduzir abstração de *Profiles* ou *Adapter Setup*. Exemplo: comando `council auth anthropic --key xyz`, permitindo que o próprio Council cuide do "Headless request" internamente (usando libs HTTP nativas do Python) quando a CLI global não for encontrada.

## 4. Telemetria CLI e Monitoramento de Custos 

Agentes gastam tokens e ciclos de planejamento demoram. Rodar pipelines longos em APIs externas requer clareza nos gastos.
*   **Dashboard TUI de Analítica:** Adicionar uma view dedicada (`council metrics` ou atalho `Ctrl+M` na TUI) exibindo um gráfico de uso/sessões. Extrair ativamente (quando possível) estimativas de tempo de computação e tokens consumidos, agregando os dados no BD local para histórico descritivo.
*   **Rate Limits e Retry Decorators:** Tratar proativamente `429 Too Many Requests` com *backoff exponencial* na classe de execução (`Executor`), ao invés de abortar o processo headless abruptamente e gerar falsos negativos na esteira.

## 5. Edição in-place e Integração com Editores via CLI 

O Council exibe o output nativamente na TUI textualmente (ou guarda em clipboard). O processo final de dev passa por consolidar e aplicar os resultados em seus artefatos fonte.
*   **Aplicação Direta de Patch (Diffing/Merge):** Incluir suporte a saídas no formato "Patch" (diff unix). Ao detectar um diff validado na última etapa, a TUI renderiza a intenção visualmente na tela (verde/vermelho) e apresenta a decisão (via checkpoint interativo): `[Y]es to apply patch, [N]o, [A]djust?` 
*   **Abertura do Editor (`$EDITOR`):** Um atalho na TUI (`Ctrl+E`) para injetar instantaneamente o buffer da resposta da rede textual ativa, no Neovim, VSCode ou editor default do ambiente do desenvolvedor.

## 6. Templates e Marketplace no Terminal

*   **Comunidade de Fluxos (`flow.json`):** Facilitar a importação de topologias avançadas criadas pela comunidade através de um registry focado na linha de comando.
    *   *Ex:* `council flow install auto-code-refactor`
    *   *Ex:* `council flow install sec-audit-pipeline`

> Estas implementações preparam a migração do Council de um excelente orquestrador arquitetural de MAS, para um Asset Produtivo Indispensável (DevTool de Prateleira) completamente nativo no Terminal do programador moderno.
