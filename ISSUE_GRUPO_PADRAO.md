# Autorização do Laudos precisa de uma passada

> Texto pronto para abrir como issue no GitHub (`altcomtecnologia-IA/altcom365`,
> branch `v2-api`). Este ambiente não tem permissão de escrita na API do
> GitHub deste repositório, então a abertura precisa ser manual — copie o
> conteúdo abaixo (título + corpo) direto para o formulário de nova issue.
>
> Dois achados, os dois no Laudos (fora do escopo do módulo Clientes e
> Processos, que está isento de ambos desde a primeira linha — ver
> `clientes/auth.py`). Nenhum dos dois deve ser corrigido junto com um PR
> de outra coisa — cada correção merece o próprio PR, testado contra a
> lista real de usuários/rotas antes do merge, pelo mesmo motivo que
> adiou o achado 1: mexer no que está em produção sem levantar o estado
> real primeiro é como se troca acesso de gente de verdade sem querer.

## Título sugerido

`Autorização do Laudos precisa de uma passada: _GRUPO_PADRAO fixo + rotas sem @requer`

## Corpo

### Achado 1 — `_GRUPO_PADRAO` em `altcom_auth/middleware.py` concede acesso total a qualquer JWT válido

#### O que acontece hoje

Em `altcom_auth/middleware.py`, dentro do hook `before_request` registrado
por `registrar()`, qualquer JWT que passe na validação de assinatura (RS256
via Cloudflare Access) recebe automaticamente o grupo `_GRUPO_PADRAO =
'tecnico_gestao'`:

```python
_GRUPO_PADRAO = 'tecnico_gestao'
...
g.identidade = {
    "email": email,
    "grupo": _GRUPO_PADRAO,
    "capacidades": GRUPOS[_GRUPO_PADRAO],
}
```

Não existe resolução por e-mail nem checagem contra uma lista de usuários —
o valor é fixo. Na prática, isso significa que **qualquer pessoa com um
e-mail válido no Cloudflare Access da organização** (não uma lista
específica de usuários autorizados) recebe as capacidades de
`tecnico_gestao`, o grupo de maior privilégio hoje mapeado em `GRUPOS`.

#### Por que não foi corrigido ainda

Identificado durante o desenvolvimento do módulo "Clientes e Processos"
(Fase 1, passo 3, 30/08/2026 — ver decisão 1 daquele passo). A correção
óbvia — resolver o grupo por e-mail, com um mapa `email → grupo` explícito
— foi **deliberadamente adiada**, porque:

- `usuarios.yaml` (ou equivalente) hoje só lista um usuário real (o
  fundador da Altcom). Aplicar a correção sem antes levantar a lista
  completa de quem usa o Laudos hoje e qual grupo cada um deveria ter
  cortaria o acesso de qualquer pessoa não listada — inclusive,
  possivelmente, de usuários legítimos em uso ativo.
- Esse middleware é compartilhado com o Laudos (o app principal, branch
  `main`/serviço `altcom365`, em produção). Uma correção malfeita aqui tem
  potencial de causar uma interrupção de acesso em produção fora do escopo
  do trabalho que a motivou.

Por isso a decisão foi **isolar** o módulo novo (Clientes e Processos) da
falha em vez de corrigi-la no middleware compartilhado: `clientes/auth.py`
não lê `g.identidade` em nenhum ponto, revalida o JWT por conta própria e
resolve papel/capacidades por uma tabela própria (`usuario`, no schema do
portal), com fail-closed em qualquer caso que não seja "e-mail ativo com
papel mapeado". O código está isento do bug atual, mas o bug em si
continua presente em produção para o Laudos.

#### O que precisa acontecer antes de corrigir

1. Levantar a lista real de e-mails que devem ter acesso ao Laudos hoje, e
   com qual nível (o que hoje `GRUPOS` define).
2. Decidir o mecanismo de resolução (mapa estático em código/config,
   tabela em banco, etc. — dado que o módulo Clientes já introduziu uma
   tabela `usuario` no schema do portal, vale avaliar se o Laudos deveria
   passar a consultar essa mesma tabela em vez de um mapa separado).
3. Aplicar a correção em um PR dedicado, testado contra a lista real antes
   do merge, para não repetir o mesmo risco que motivou o adiamento.

#### Impacto se não for corrigido

Qualquer credencial válida do Cloudflare Access da organização hoje abre
acesso total ao Laudos (`laudo:ler` e demais capacidades de
`tecnico_gestao`), independente de quem seja a pessoa. Não afeta o módulo
Clientes e Processos (isolado desde a primeira linha), mas é uma falha real
de controle de acesso no restante da aplicação.

---

### Achado 2 — Quatro rotas do Laudos sem `@requer` (pré-existente, achado no passo 3, 30/08/2026)

Identificado durante o smoke test do módulo Clientes e Processos — não é
consequência de nada mudado nesse passo, já estava assim.

#### O que acontece hoje

Em `app.py`, quatro rotas não têm o decorator `@requer(...)` que todas as
outras rotas de negócio têm:

- `GET /milvus-status`
- `POST /sincronizar-milvus`
- `GET /sync-status`
- `POST /sync-clientes`

Continuam atrás do `before_request` global de `altcom_auth/middleware.py`
— ou seja, ainda exigem um JWT válido, não estão abertas a qualquer um —
mas não têm a checagem de capacidade (`laudo:ler`, `laudo:criar`, etc.)
que protege o resto da aplicação. Com o bug do Achado 1 em vigor, isso
hoje não muda o resultado prático (todo JWT válido já cai em
`tecnico_gestao`, que tem tudo); mas os dois problemas são independentes
— corrigir só o Achado 1 sem revisar estas quatro rotas deixaria alguém
com um papel restrito (ex.: `tecnico_analistas`, que não pode
`laudo:excluir`) acessando rotas que nenhum `@requer` protege.

#### Um detalhe à parte, sem relação com autorização

`GET /sync-status` e `POST /sync-clientes` referenciam `ClientesMap` e
`DispositivosMap`, dois nomes que `app.py` não importa (confirmado:
`models.py`, onde essas classes vivem, não é usado por `app.py` hoje — ver
comentário em `migrations/env.py`). Chamar qualquer uma das duas hoje
levanta `NameError` em tempo de execução, não erro de autorização. Rotas
mortas, não rotas inseguras — mas vale corrigir ou remover na mesma
passada, já que alguém vai abrir o arquivo de qualquer forma.

#### O que precisa acontecer antes de corrigir

1. Confirmar, rota por rota, qual capacidade cada uma deveria exigir (as
   quatro parecem operações de sincronização/status — plausivelmente
   `laudo:criar` ou uma capacidade própria de sincronização, a decidir).
2. Decidir o destino de `/sync-status` e `/sync-clientes` primeiro
   (corrigir o `NameError` importando `models.py`, ou remover as rotas se
   `ClientesMap`/`DispositivosMap` não são mais usadas) — não faz sentido
   proteger uma rota que quebra de qualquer forma.
3. Aplicar em PR dedicado, separado do Achado 1.

#### Impacto se não for corrigido

Nenhuma exposição sem autenticação — o `before_request` global já exige
JWT em todas as quatro. O risco é de autorização granular: qualquer
usuário autenticado, independente do papel que devesse ter, pode acionar
essas quatro rotas.

---

### Achado relacionado — `sessao_log` e `usuario.ultimo_acesso` existem, nada escreve neles

Fora do escopo dos dois achados acima (não é autorização, é observabilidade/
auditoria), registrado aqui pelo mesmo motivo: achado durante o passo 3,
sem dono nem prazo ainda.

A tabela `sessao_log` (`portal/models.py`) e a coluna
`usuario.ultimo_acesso` existem no schema desde a Fase 1, mas nenhuma rota
ou middleware grava nelas hoje — nem o `before_request` compartilhado, nem
`clientes/auth.py`. Não bloqueava o passo 3 (nenhuma decisão pediu login
tracking) e não é um bug — é uma lacuna sem dono. Registrando para não
virar, daqui a alguns meses, alguém abrir `sessao_log` vazia e concluir
que está quebrada. Quando tiver dono: decidir se quem escreve é o
middleware compartilhado (um ponto só, cobre Laudos e Clientes) ou cada
módulo por conta própria (mais isolado, duplica a escrita).
