# Project rules

Projeto de portfolio, escrito por um estudante construindo base em
ciencia de dados. Otimize para clareza e para o codigo parecer escrito
a mao, nunca para esperteza ou concisao extrema.

## Code level

Escreva codigo que um estagiario escreveria e entenderia.

PERMITIDO
- pandas, numpy, matplotlib, seaborn, scikit-learn, sqlite3
- funcoes simples com nomes claros
- for loops explicitos quando lerem melhor que truques vetorizados
- list comprehension simples, um nivel so
- f-strings

NAO PERMITIDO sem eu pedir
- classes, decorators, generators, context managers
- lambda alem de uma linha trivial
- comprehensions aninhadas, method chaining com mais de 3 passos
- async, multiprocessing, threading
- typing avancado (Protocol, TypeVar, Generic)
- frameworks de config, injecao de dependencia, ABCs
- one-liners que juntam varias operacoes

## Style

- Uma funcao faz uma coisa. Menos de 20 linhas.
- Docstring so quando o proposito nao for obvio pelo nome. Uma linha,
  sem template "Recebe / Devolve".
- Comentario explica POR QUE, nunca O QUE. Se a linha ja diz, corta.
- A maioria das funcoes nao deve ter comentario nenhum.
- Sem banner de secao, sem divisor ASCII, sem cabecalho decorativo.
- Docstring de modulo: 1 a 3 linhas. Sem redacao.
- Explicito melhor que esperto, mas sem ser verboso a toa.
- Nomes de variavel por extenso: station_id, nao sid.
- Sem abstracao prematura. Repita antes de generalizar.

## Language

- Codigo em ingles: nomes de funcao, variaveis, colunas, arquivos.
- Comentarios, docstrings e README em portugues.
- Mensagens de commit em ingles, com os prefixos convencionais
  (feat, fix, docs, refactor, chore).
- Comentarios curtos e diretos, minusculas, sem ponto final.
- Sem acento nos comentarios, para evitar problema de encoding.

## Workflow

- Explique a abordagem antes de escrever codigo. Espere confirmacao.
- Depois de escrever, liste as funcoes que eu preciso saber explicar
  numa entrevista.
- Nao introduza biblioteca que ainda nao esta no requirements.txt sem
  perguntar antes
