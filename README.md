# Go Sell Demo

Demonstração do **Go Sell**: venda assistida para feiras, congressos e ações comerciais.

Quem assiste percorre, em 20–30 minutos: administrador prepara o evento → vendedor vende no catálogo → cliente recebe a nota de retirada → a venda vai para o ERP → administrador vê clientes, estoque e financeiro.

Os dados são fictícios. O ERP é **simulado** dentro do próprio sistema; não há maquininha real nem impressora USB.

## Como subir

Requisito: **Python 3.11+**.

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Abra [http://localhost:5000](http://localhost:5000). Se a porta 5000 já estiver em uso, suba em outra:

```bash
set PORT=5001
python app.py
```

Em produção (opcional):

```bash
gunicorn -w 1 -b 0.0.0.0:8000 main:app
```

Use **1 worker** (SQLite e a fila do ERP simulado rodam no processo).

## Contas da demo

Todas entram por `/entrar` (botão **Entrar** na tela inicial), senha `demo`.

| Tipo | Login | O que vê |
|------|-------|----------|
| Administrador | `admin` | Todo o painel, em todos os eventos |
| Vendedor | `ana`, `bruno` | Catálogo, checkout, minhas vendas, estoque |
| Marketing | `marina` | Dashboard, transações (consulta) e promoções da feira |
| Personalizado | `carlos` | Dashboard, estoque e transações da feira |

## O que já vem montado

- **Feira Demo Go Sell** (em andamento) e **Congresso Demo Go Sell** (encerrado, ~45 dias atrás)
- 10 produtos vinculados ao ERP (estoque alto, abaixo do mínimo e zerado) + 3 que vieram do ERP
- 5 promoções (percentual, valor fixo, "a partir de", compre-e-leve, kit) e 2 regras de brinde
- 9 clientes fictícios com CPF, recompras e ~27 vendas em todas as formas de pagamento
- Retiradas pendentes, movimentações manuais e 1 pedido em aberto (para mostrar a retomada)
- Integração ERP ativa: vendas já enviadas, 1 com erro (para mostrar o reenvio)
- Botão **Reiniciar** no admin restaura este estado

## Roteiro rápido

1. `/` → **Entrar** como `admin`
2. **Feira Demo Go Sell** → dashboard com metas e gráficos
3. **Clientes** → carteira, perfil de um cliente, exportar planilha/PDF
4. **Estoque** → filtrar abaixo do mínimo → entrada ou saída em lote
5. **Promoções** → descontos e **Brindes** (caneca acima de R$ 500)
6. **Integração ERP** → testar conexão, **Sincronizar com ERP** (traz 1 produto novo e 1 reajuste), reenviar o pedido com erro
7. **Usuários** → criar um usuário de Marketing e escolher os eventos dele
8. Sair → entrar como `ana`
9. Catálogo → 3 caixas de luva (ganham 1 espelho) → carrinho
10. Pagamento → digitar o CPF `390.817.254-32` (cliente que só existe no ERP) → dados preenchidos
11. Escolher a forma de pagamento → informar um código AUT → pedido confirmado
12. Abrir a nota (com o brinde) → imprimir
13. Admin → **Integração ERP**: o pedido sai da fila e aparece como enviado
14. Transações → **Movimentações de estoque**, PDF do pedido, Financeiro
15. (Opcional) Entrar como `marina` para mostrar o painel restrito
16. (Opcional) Encerrar operações → vendedor não vende mais

## Testes

```bash
python seed/smoke_test.py       # roteiro completo (reinicia a demo antes e depois)
python seed/test_promo_rules.py # regras de promoção
```

## Variáveis de ambiente

Copie `.env.example` para `.env` se quiser alterar a senha do admin. A chave de sessão é gravada automaticamente em `totem.env`.

| Variável | Para quê |
|----------|----------|
| `TOTEM_ADMIN_USER` / `TOTEM_ADMIN_PASS` | Conta do administrador (padrão `admin` / `demo`) |
| `DEMO_ERP_LATENCY` | Espera simulada do ERP, em segundos (padrão `0.6`) |
| `DEMO_ERP_FAIL_RATE` | Chance (0 a 1) de o ERP simulado recusar um pedido |
| `TOTEM_ERP_WORKER=0` | Desliga o envio automático da fila do ERP |

Mais detalhes em [docs/go-sell-demo.md](docs/go-sell-demo.md).
