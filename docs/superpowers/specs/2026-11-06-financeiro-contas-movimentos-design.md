# Financeiro — Contas e movimentos — Design

## Objetivo

Entregar a Etapa 1 do Financeiro do Cruz Fit: contas financeiras com saldo inicial datado, histórico append-only de créditos e débitos, aportes, transferências entre contas e estornos vinculados ao lançamento original, com autorização de gerente/superusuário, auditoria, idempotência e interface server-rendered responsiva.

## Decisões de arquitetura

- A implementação será uma app Django nova, `financeiro`, porque a Etapa 1 é um domínio próprio e não deve acoplar o ledger futuro ao Caixa.
- O repositório atual usa Django templates e function views; não há Django REST Framework instalado nem ViewSets. As ações não-CRUD serão `POST` namespaced com CSRF, protegidas no servidor, e encaminharão toda escrita a serviços transacionais.
- `ContaFinanceira` guarda nome, saldo inicial e `data_corte_financeiro`. A data é inclusiva: o saldo inicial representa a abertura desse dia; movimentos anteriores são rejeitados e movimentos na data ou depois são aceitos.
- `LancamentoFinanceiro` é um ledger append-only sem campo de tipo de negócio. Ele guarda conta, direção (crédito/débito), valor, data, motivo, responsável e vínculo de estorno. Os fluxos de negócio continuam tendo modelos próprios:
  - `AporteFinanceiro` cria um crédito e não é receita operacional.
  - `TransferenciaFinanceira` cria um débito na origem e um crédito no destino na mesma transação.
  - `EstornoFinanceiro` cria o lançamento inverso e aponta para o lançamento original sem apagá-lo.
- Cada escrita tem chave única e assinatura dos dados relevantes. Replay com mesma chave e assinatura retorna a operação existente; mesma chave com outra assinatura falha antes de qualquer nova escrita.
- Cada operação e criação de conta grava `EventoAuditoriaFinanceiro` na mesma transação, com responsável, motivo, chave, ação e payload JSON.

## Fluxos

1. O gerente cadastra a conta informando saldo inicial e data de corte. A conta nasce com saldo disponível igual ao saldo inicial; não nasce com receita operacional.
2. O gerente registra aporte. O serviço valida a data contra o corte, cria `AporteFinanceiro` e seu crédito no ledger; o saldo aumenta, mas o cálculo de receita operacional permanece inalterado.
3. O gerente transfere entre duas contas. O serviço valida contas distintas, data, valor e permissão; dentro de `transaction.atomic()` cria a operação, o débito e o crédito. Qualquer exceção desfaz todos os três registros.
4. O gerente estorna um lançamento elegível. O serviço cria `EstornoFinanceiro` e o lançamento inverso, preservando o original e impedindo novo estorno por idempotência/constraint.
5. A tela de Financeiro mostra saldo disponível, receita operacional desta etapa (zero), contas, histórico e formulários das três operações. Usuário vendedor recebe `403` em qualquer `POST` de escrita, inclusive acesso direto à URL.

## Dados e consultas

O saldo atual de uma conta será calculado como `saldo_inicial + créditos - débitos` usando `Decimal`. Transferências não entram em receita operacional e não alteram o saldo total consolidado. A consulta de histórico ordena por data de movimento e criação, mostrando direção, valor, operação, motivo, responsável e vínculo de estorno.

## Erros e segurança

Serviços rejeitam valor não positivo, data anterior ao corte, conta inativa, conta de origem igual à de destino, motivo ausente, chave ausente ou chave conflitante. As mensagens serão orientadas à correção. A verificação de gerente/superusuário ocorre dentro do serviço e também na view; a view não é a única barreira.

## Testes e evidências

Os testes serão escritos antes do código e executados em ciclos RED/GREEN. Serão cobertos: corte inclusivo e véspera rejeitada sem escrita, saldo inicial fora da receita, aporte, transferência de R$ 80 entre R$ 500/R$ 100, replay equivalente, replay conflitante, rollback após falha entre débito/crédito, estorno com histórico preservado, auditoria e vendedor bloqueado. A interface será exercitada em desktop e viewport móvel 390×844 com banco e mídia descartáveis.

## Fora desta etapa

Recebimentos Pix/cartão, integração com Caixa, contas a pagar, fechamento mensal, integração bancária, conciliação automática, publicação na `main` e ativação no cliente.
