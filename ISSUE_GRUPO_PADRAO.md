# `_GRUPO_PADRAO` em `altcom_auth/middleware.py` concede acesso total a qualquer JWT válido

> Texto pronto para abrir como issue no GitHub (`altcomtecnologia-IA/altcom365`,
> branch `v2-api`). Este ambiente não tem permissão de escrita na API do
> GitHub deste repositório, então a abertura precisa ser manual — copie o
> conteúdo abaixo (título + corpo) direto para o formulário de nova issue.

## Título sugerido

`Bug: _GRUPO_PADRAO em altcom_auth/middleware.py concede grupo 'tecnico_gestao' a qualquer JWT válido`

## Corpo

### O que acontece hoje

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

### Por que não foi corrigido ainda

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

### O que precisa acontecer antes de corrigir

1. Levantar a lista real de e-mails que devem ter acesso ao Laudos hoje, e
   com qual nível (o que hoje `GRUPOS` define).
2. Decidir o mecanismo de resolução (mapa estático em código/config,
   tabela em banco, etc. — dado que o módulo Clientes já introduziu uma
   tabela `usuario` no schema do portal, vale avaliar se o Laudos deveria
   passar a consultar essa mesma tabela em vez de um mapa separado).
3. Aplicar a correção em um PR dedicado, testado contra a lista real antes
   do merge, para não repetir o mesmo risco que motivou o adiamento.

### Impacto se não for corrigido

Qualquer credencial válida do Cloudflare Access da organização hoje abre
acesso total ao Laudos (`laudo:ler` e demais capacidades de
`tecnico_gestao`), independente de quem seja a pessoa. Não afeta o módulo
Clientes e Processos (isolado desde a primeira linha), mas é uma falha real
de controle de acesso no restante da aplicação.
