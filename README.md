# GRIT — Git Repository Inspector for Teachers

O **GRIT** apoia professores e monitores de cursos de computação e engenharia de software na auditoria do histórico de versionamento de projetos práticos. Ele analisa repositórios locais ou remotos e mostra como o trabalho foi distribuído na equipe, quanto cada aluno contribuiu (commits, linhas e autoria sobrevivente), como as branches foram usadas e se as mensagens de commit seguem boas práticas.

- **Sem dependências externas:** apenas a biblioteca padrão do Python e o Git instalado.
- **Dois modos de uso:** interface gráfica (Tkinter) e linha de comando.

---

## Requisitos

- Python 3.9 ou superior (com `tkinter` para o modo gráfico)
- Git disponível no `PATH`

## Instalação

```bash
git clone https://github.com/profdcallegari/grit.git
cd grit

# Opção 1: executar direto, sem instalar
python -m grit

# Opção 2: instalar o comando `grit` (modo editável)
pip install -e .
grit
```

### Usando o `uv`

Com o [uv](https://docs.astral.sh/uv/) não é preciso criar o ambiente virtual manualmente:

```bash
# Cria o .venv e instala o projeto (modo editável) a partir do uv.lock
uv sync

# Executa sem ativar o ambiente
uv run grit
uv run grit --cli https://github.com/exemplo/projeto-alunos.git --roster turma.json
uv run python -m grit -v

# Compila o pacote (gera sdist e wheel em dist/)
uv build

# Instala o comando `grit` globalmente, isolado do projeto
uv tool install .
grit --help
```

Para rodar o wheel gerado em outro ambiente: `uv pip install dist/grit-0.1.0-py3-none-any.whl`.

## Uso

```bash
# Interface gráfica
python -m grit

# Interface gráfica com repositório (e roster) pré-carregados
python -m grit https://github.com/exemplo/projeto-alunos.git --roster turma.json

# Linha de comando (sem GUI)
python -m grit --cli https://github.com/exemplo/projeto-alunos.git --roster turma.json

# Repositório local, sem git blame, com log detalhado
python -m grit --cli ./projeto-alunos --no-blame -v
```

### Opções

| Opção | Descrição |
|---|---|
| `repo` | Caminho local ou URL (`https://` ou `git@`) do repositório |
| `--roster`, `-r` | Arquivo JSON de mapeamento de alunos (veja abaixo) |
| `--cli` | Força o modo terminal, sem interface gráfica |
| `--no-blame` | Não calcula a autoria sobrevivente (`git blame`), que é lenta em repositórios grandes |
| `-v`, `-vv` | Exibe o log no terminal (informações / detalhes) |

Se a interface gráfica não puder ser carregada, o GRIT recorre automaticamente ao modo CLI.

### Arquivo de turma (`roster.json`)

Associa cada aluno a todos os e-mails que ele usa nos commits:

```json
{
  "Maria Silva": ["maria@universidade.edu.br", "maria.silva@gmail.com"],
  "João Souza": ["joao@universidade.edu.br"]
}
```

O `.mailmap` do repositório também é respeitado. Quando existe roster ou `.mailmap`, autores fora deles são sinalizados como identidades não confiáveis.

---

## Métricas e heurísticas

1. **Autoria e normalização:** unifica apelidos e múltiplos e-mails via `.mailmap` e `roster.json`.
2. **Volume e churn:** commits, linhas adicionadas (`+`), removidas (`-`) e saldo por aluno. Ignora diretórios de dependências (`node_modules`, `vendor`, `.venv`) e lockfiles (`package-lock.json`, `poetry.lock` etc.).
3. **Boas práticas de commit:**
   - **Conventional Commits:** validação por expressão regular (`feat:`, `fix:`, `docs:` etc.).
   - **Commits diretos na branch principal:** commits que entraram em `main`/`master` sem passar por branch secundária ou PR.
   - **Mensagens vagas:** como `"update"`, `"fix"`, `"ajustes"` ou com menos de 4 caracteres.
4. **Branches:** lista locais e remotas com estado (`principal`, `mesclada`, `ativa` ou `abandonada`), data e autor do último commit. É *abandonada* a branch não mesclada cujo último commit é mais de 30 dias anterior ao commit mais recente do repositório.
5. **Concentração do trabalho:**
   - Percentual de commits e de linhas (adicionadas + removidas) por aluno e **índice de Gini** (0 = distribuição igual, 1 = tudo com uma pessoa). O log alerta quando uma pessoa concentra 60% ou mais dos commits.
   - **Autoria sobrevivente:** com `git blame` na branch padrão, conta quantas linhas do código atual pertencem a cada aluno (ignora lockfiles, dependências e binários; limite de 1500 arquivos). Pode ser desativada na GUI ou com `--no-blame`; é mais lenta em clones sem blobs.
6. **Linha do tempo:** aba **Atividade** com mapa de calor de commits por semana (iniciada na segunda-feira) e por aluno.
7. **Identidades não confiáveis:** destaca e-mails ausentes do roster/`.mailmap` ou genéricos (`root@`, `@localhost`, `(none)`), que de outra forma apareceriam como "alunos fantasma".
8. **Links para o navegador:** na GUI, duplo clique em autor, branch ou commit (aba **Commits**) abre o item no GitHub ou GitLab (inclusive instâncias cujo host contenha `github`/`gitlab`). Requer que o remoto `origin` seja um desses; caso contrário, o motivo aparece no log.

---

## Arquitetura e decisões técnicas

- **Biblioteca padrão apenas:** `subprocess`, `tkinter`, `dataclasses`, `json`, `re`, `threading`.
- **Git CLI com streaming:** usa `git log --numstat` com delimitadores customizados para processar históricos grandes com pouca memória, e clones parciais (`--filter=blob:none`) para evitar baixar todos os blobs.
- **Repositórios locais ou remotos:** URLs são clonadas em diretórios temporários (`tempfile`), removidos ao final da análise.
- **GUI responsiva:** rede e parsing do Git rodam em threads separadas da interface Tkinter.

## Estrutura do projeto

```text
grit/
├── README.md
├── pyproject.toml
└── grit/
    ├── __main__.py          # Ponto de entrada (CLI e seletor de modo)
    ├── gui.py               # Interface gráfica (Tkinter/ttk)
    ├── activity_panel.py    # Mapa de calor de atividade semanal (Canvas)
    ├── log_panel.py         # Aba de log (handler thread-safe + painel)
    └── core/
        ├── analyzer.py      # Motor analítico e regras de negócio
        ├── git_client.py    # Chamadas de baixo nível ao Git
        ├── links.py         # URLs web (GitHub/GitLab) de branches, commits e autores
        ├── metrics.py       # Atividade semanal, Gini e concentração
        ├── models.py        # Dataclasses (commits, autores, relatórios)
        └── normalizer.py    # Mapeamento de identidades e aliases
```

---

## Licença

Distribuído sob a licença do arquivo [LICENSE](LICENSE).

Copyright © 2026 Daniel Callegari
