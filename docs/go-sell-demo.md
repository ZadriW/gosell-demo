# Go Sell Demo — funcionalidades básicas

Especificação para implementar a **demonstração do produto Go Sell** em outra pasta, a partir do sistema já desenvolvido para a Odonto Master.

Este documento descreve o **núcleo demonstrável**. Não inclui marca, CNPJ, CRO, Wake Commerce nem dados reais da Odonto Master.

---

## 1. Objetivo da demo

Permitir que outra empresa use o Go Sell em uma reunião de 15–20 minutos e percorra, sozinha ou guiada:

1. Administrador prepara um evento (produtos, estoque, vendedor, promoção).
2. Vendedor vende no catálogo, cobra e confirma o AUT.
3. Cliente recebe a nota de retirada.
4. Administrador vê estoque baixado, transação e financeiro.

Quem assiste deve entender o produto **sem** precisar conhecer odontologia, ERP ou o ambiente de produção da Odonto Master.

---

## 2. Princípios

| Princípio | Na prática |
|-----------|------------|
| Marca Go Sell | Logo, cores e textos do produto. Zero “Odonto Master”, CRO ou CNPJ de cliente. |
| Dados fictícios | Evento, produtos, vendedores e vendas de seed. Nada copiado do banco real. |
| Isolado | Pasta, repositório, `.env` e SQLite próprios. |
| Resetável | Botão **Reiniciar** (ou script) volta a demo ao estado inicial em segundos. |
| Contas conhecidas | Admin e vendedor com senha simples, visíveis na tela de boas-vindas ou num cartão impresso. |
| Sem dependência externa | Sem Wake, sem maquininha real, sem impressora USB obrigatória. AUT é digitado. |
| Responsivo | Notebook, tablet e celular — o mesmo fluxo. |

---

## 3. Fora do escopo da demo v1

Não implementar (ou deixar desligado) o que é específico da Odonto Master ou avançado demais para a primeira demonstração:

- Integração Wake Commerce (sync, importação on-demand, token).
- Campo CRO (UF + número) no checkout.
- Importação de planilha `.xls` ligada a ERP.
- Cache de imagens de um e-commerce externo.
- Impressão silenciosa / térmica no servidor.
- Relatório financeiro em PDF no formato da Odonto Master (um PDF simples basta, ou só a tela).
- Multi-empresa / multi-tenant no mesmo banco.
- App nativo, totens físicos, autoatendimento sem login.

Fase 2 (depois da demo andar): entrega futura / backorder, handover, metas de faturamento, combo de promoções complexo, exportação Excel completa.

---

## 4. Papéis e contas de seed

Três papéis. Só dois logam na demo.

| Papel | Acesso | Para mostrar |
|-------|--------|----------------|
| **Administrador** | `/admin` | Eventos, estoque, vendedores, promoções, transações, financeiro. |
| **Vendedor** | `/vendedor` | Catálogo, checkout, AUT, minhas vendas, estoque (leitura). |
| **Cliente** | só a nota pública | Comprovante assinado; não tem login. |

Sugestão de contas (trocar na instalação, mas manter óbvias na demo):

| Usuário | Login | Senha |
|---------|-------|--------|
| Admin | `admin` | `demo` |
| Vendedor A (no evento ativo) | `ana` | `demo` |
| Vendedor B (opcional, segundo caixa) | `bruno` | `demo` |
| Marketing (v2) | `marina` | `demo` |
| Personalizado (v2) | `carlos` | `demo` |

Tela inicial (`/`): botão **Entrar** (login único em `/entrar`, desde a v2), contas da demo visíveis, marca Go Sell, sem rodapé de cliente.

---

## 5. Dados iniciais (seed)

Um único **evento ativo** já montado, para a demo não começar vazia.

**Evento:** `Feira Demo Go Sell` (cor de badge visível).

**Catálogo (8–12 produtos fictícios), por exemplo:**

- Categorias: Equipamentos, Consumíveis, Acessórios.
- Mix: uns com estoque alto, uns abaixo do mínimo, um zerado (para mostrar “sem estoque”).
- SKU, nome, preço, foto local (placeholder), estoque e mínimo no evento.

**Promoções ativas no evento (o mínimo que impressiona):**

1. Desconto **percentual** em um produto.
2. Desconto **fixo** em outro.
3. Uma regra de **pacote** (ex.: “a partir de 3 unidades”).

Não precisa dos seis `rule_type` da Odonto Master (`percent`, `fixed`, `bogo`, `min_bundle`, `exact_bundle`, `combo_bundle`) na v1. Três regras visíveis no catálogo bastam.

**Vendas de seed (opcional):** 4–8 pedidos confirmados no evento, de datas recentes, para o dashboard e o financeiro não abrirem zerados. Incluir um pedido **pendente** (carrinho abandonado) para mostrar retomada.

---

## 6. Funcionalidades obrigatórias

Espelham o Go Sell da Odonto Master, sem o acoplamento dela.

### 6.1 Acesso e sessão

- [ ] Tela de boas-vindas com os dois acessos.
- [ ] Login admin e login vendedor (e-mail + senha).
- [ ] Logout.
- [ ] Vendedor só opera se estiver vinculado a um **evento ativo**.
- [ ] CSRF nos POST; cookie de sessão HTTP-only.
- [ ] Modo claro / escuro por painel (admin e vendedor).

### 6.2 Admin — global

Menu: **Produtos**, **Eventos**, **Vendedores**, **Financeiro**.

**Produtos (biblioteca)**

- [ ] Lista com busca, categoria, situação (ativo/inativo).
- [ ] Cadastro manual: nome, SKU, preço, categoria, foto (upload ou URL local).
- [ ] Ativar / inativar.
- [ ] Detalhe do produto (dados + últimos movimentos daquele SKU, mesmo que simples).

**Eventos**

- [ ] Listar ativos e arquivados.
- [ ] Criar / editar (nome, cor do badge).
- [ ] Arquivar / restaurar.
- [ ] Encerrar operações / reabrir (evento continua visível, mas venda e edição de estoque bloqueadas — útil na demo: “fim do congresso”).
- [ ] Entrar no evento (dashboard + subnav).

**Vendedores**

- [ ] Cadastrar, editar, ativar/inativar.
- [ ] Vincular a um evento.
- [ ] Ver vendas daquele vendedor (lista simples).

**Financeiro**

- [ ] Filtro por evento e período.
- [ ] Totais: pedidos, itens, faturamento.
- [ ] Quebra por forma de pagamento (cartão / PIX).
- [ ] Exportar PDF ou imprimir a tela (um dos dois).

**Reiniciar sistema**

- [ ] Restaura seed (produtos, evento, vendedores, transações de exemplo). Confirmação clara. Uso só na demo.

### 6.3 Admin — dentro do evento

Subnav: **Dashboard**, **Estoque**, **Transações**, **Promoções**.

**Dashboard do evento**

- [ ] KPIs: vendas, faturamento, itens, pendentes.
- [ ] Metas (opcional na v1): faturamento e/ou volume, com barra de progresso.
- [ ] Atalhos para estoque e transações.

**Estoque do evento**

- [ ] Tabela: produto, preço no evento, vendas, quantidade, mínimo, situação.
- [ ] Filtros: busca, categoria, situação; botão Aplicar (rolar até a tabela).
- [ ] Adicionar produto da biblioteca ao evento (estoque inicial 0).
- [ ] Entrada, saída, ajuste de quantidade (com motivo).
- [ ] Alterar preço e estoque mínimo no evento.
- [ ] Remover produto do evento (se política do núcleo permitir).
- [ ] Histórico de movimentações **por produto** (data, tipo, delta, saldo, motivo, usuário).
- [ ] Legenda de situação (OK / baixo / zerado / inativo).

**Transações do evento**

- [ ] Lista de pedidos do evento (pago, pendente, cancelado, estornado).
- [ ] Filtros: vendedor, status, data, código do pedido / nome do cliente; Aplicar → tabela.
- [ ] Detalhe da linha: itens, totais, promoção aplicada, cliente, pagamento, AUT.
- [ ] Estorno de venda confirmada (com confirmação).
- [ ] Observação na nota (texto livre no comprovante).
- [ ] Link da **nota de retirada** (pedido confirmado).
- [ ] Atualização periódica da lista (polling), como no painel atual.

**Promoções do evento**

- [ ] Listar, criar, editar, ativar/desativar, excluir.
- [ ] Associar a um ou mais produtos do evento.
- [ ] Na v1: `percent`, `fixed` e um tipo de pacote (`min_bundle` ou equivalente).
- [ ] Badge no catálogo do vendedor quando o produto está em promoção.

### 6.4 Vendedor

Menu: **Catálogo**, **Minhas vendas**, **Estoque**.

**Catálogo / venda**

- [ ] Só produtos do evento ativo.
- [ ] Busca, categorias, foto, preço, estoque ao vivo (polling).
- [ ] Carrinho lateral: quantidade, subtotal, promoção recalculada no servidor (`/api/carrinho/cotacao`).
- [ ] Impedir (ou avisar) venda acima do estoque, salvo regra de backorder — na demo, **não vender sem estoque**.
- [ ] Timeout de inatividade no checkout (volta ou bloqueia a tela).
- [ ] Se o evento estiver com operações encerradas: catálogo **somente leitura**, sem nova venda.

**Pagamento**

- [ ] Resumo dos itens e totais (o mesmo valor da cotação).
- [ ] Dados do cliente: **nome** (obrigatório). Telefone ou e-mail opcional. Sem CRO.
- [ ] Forma de pagamento: cartão (com parcelas) e PIX.
- [ ] Cria transação **pendente** (estoque ainda não baixa).
- [ ] Tela aguardando: vendedor informa o **código AUT** (simulado: qualquer código com tamanho mínimo).
- [ ] Confirmação AUT: status pago, baixa estoque, gera número do pedido e nota.
- [ ] Retomar checkout de pedido pendente (sessão caiu / aba fechada).
- [ ] Descartar pendente.

**Minhas vendas**

- [ ] KPIs do vendedor no evento (hoje / total).
- [ ] Lista de transações com os mesmos filtros essenciais (status, data, pedido).
- [ ] Pendentes e pagos; atalho para retomar pendente.
- [ ] Nota de retirada nos confirmados.

**Estoque (leitura)**

- [ ] Mesma tabela do evento, sem entrada/saída/ajuste.
- [ ] Detalhe do produto: próprias vendas daquele SKU (leitura).

### 6.5 Nota de retirada (pública)

- [ ] URL assinada (`/nota/<pedido>?t=...`). Sem token válido, não abre.
- [ ] Layout de cupom (80 mm na impressão do navegador).
- [ ] Pedido, data, itens, totais, pagamento, nome do cliente, evento.
- [ ] Botão Imprimir (`window.print()`). `print=1` abre o diálogo ao carregar (atalho da demo no balcão).
- [ ] Não é nota fiscal.

### 6.6 Regras de negócio que a demo precisa acertar

Sem isso a reunião quebra:

1. Preço e desconto do carrinho **iguais** aos gravados na transação (cotação no servidor).
2. Estoque só baixa no **AUT**, não no “ir para pagamento”.
3. Número de pedido único e visível (admin, vendedor e nota).
4. Vendedor isolado: Ana não vê vendas do Bruno; admin vê as duas.
5. Operações encerradas: consulta sim, venda/edição não.
6. Estorno devolve (ou estorna) o efeito no estoque de forma coerente.

---

## 7. Roteiro da reunião (o que a demo precisa aguentar)

Use este roteiro como teste de aceite da pasta nova.

1. Abrir `/` → **Sou administrador** → login demo.
2. Eventos → abrir **Feira Demo Go Sell** → mostrar KPIs.
3. Estoque → filtrar “abaixo do mínimo” → dar **entrada** em um produto.
4. Promoções → apontar o desconto percentual já ativo.
5. Logout → **Sou vendedor** → Ana.
6. Catálogo → buscar produto em promoção → adicionar 2 unidades → ver desconto.
7. Pagamento → nome do cliente → PIX ou cartão → criar pendente.
8. Informar AUT → pedido confirmado.
9. Abrir a nota em nova aba → imprimir (diálogo do navegador).
10. Minhas vendas → o pedido aparece como pago.
11. Voltar ao admin → transações: mesmo pedido, estoque menor, financeiro atualizado.
12. (Opcional) Encerrar operações → vendedor não vende mais → reabrir.

Se esses 12 passos funcionarem, a demo está pronta para mostrar a outra empresa.

---

## 8. UX mínima (não negociar na v1)

- Layout admin/vendedor no molde atual: topbar, nav, tabelas, filtros, flash messages.
- Confirmação em ações destrutivas (estorno, remover produto, reiniciar, encerrar operações).
- Filtros GET + **Aplicar**; **Limpar** volta ao default (sem scroll obrigatório).
- Após Aplicar em estoque/transações, scroll até a tabela (`#lista`).
- Mensagens de erro legíveis (estoque insuficiente, login inválido, evento encerrado).

---

## 9. Stack sugerida (espelhar o núcleo)

Para reaproveitar código com o mínimo de atrito:

| Camada | Como na Odonto Master |
|--------|------------------------|
| Back-end | Python 3, Flask, Flask-WTF (CSRF) |
| Banco | SQLite próprio da pasta demo |
| Front | Jinja2, CSS, JS sem framework |
| Auth | Sessão: admin **ou** vendedor, nunca os dois no mesmo cookie de forma ambígua |

Nomes técnicos internos (`totem`, ids de rota) podem permanecer no código; o que o visitante lê é **Go Sell**.

---

## 10. Estrutura sugerida da outra pasta

```
GoSell-Demo/
├── README.md                 # Como subir a demo + contas
├── docs/
│   └── go-sell-demo.md       # Cópia deste arquivo
├── app.py
├── database/                 # Schema do núcleo (sem colunas CRO/Wake)
├── static/
│   ├── css/
│   ├── js/
│   └── images/               # Logo Go Sell + fotos seed
├── templates/
│   ├── welcome.html
│   ├── admin/
│   ├── seller/
│   ├── nota.html
│   └── ...
├── seed/                     # Script ou SQL que o Reiniciar chama
└── .env.example              # SECRET_KEY, senha admin; sem WAKE_TOKEN
```

Não copie `database/totem.sqlite3` da Odonto Master. Gere um banco novo a partir do schema + seed.

---

## 11. Checklist “demo pronta”

A pasta nova pode ser apresentada quando:

- [ ] Sobe com `python app.py` (ou equivalente) e as duas contas entram.
- [ ] O roteiro da seção 7 completa sem erro e sem dados da Odonto Master.
- [ ] Reiniciar volta ao mesmo estado de seed.
- [ ] A nota abre só com token válido.
- [ ] Não há menção a Odonto Master, CRO ou Wake na interface.
- [ ] Funciona em 1280px (notebook) e ~390px (celular).

---

## 12. Relação com o sistema da Odonto Master

| Neste repositório (cliente) | Na pasta da demo (produto) |
|-----------------------------|----------------------------|
| Marca e textos Odonto Master | Marca Go Sell |
| CRO no checkout | Só nome (e contato genérico, se quiser) |
| Wake / XLS / imagens remotas | Cadastro local + fotos na pasta |
| Regras de promoção completas | 3 tipos visíveis |
| Financeiro/PDF no formato ODM | Totais claros + impressão simples |
| Impressora térmica no balcão | `window.print()` na nota |

O código da Odonto Master é a **referência de comportamento**. A demo é o **produto genérico** que você mostra; customizações da próxima empresa entram depois, por configuração, não copiando este banco.

---

## 13. Versão 2 — funcionalidades trazidas do produto (out/2026)

A demo foi atualizada a partir do código atual do Go Sell (projeto Totem), com dados fictícios. O **Controle de Aulas** ficou de fora (exclusivo da Odonto Master). Continuam fora: CRO, marca/CNPJ do cliente, backup na nuvem (Litestream).

| Funcionalidade | Onde ver | Dados da demo |
|---|---|---|
| Login único e usuários com permissões | `/entrar`, Admin → **Usuários** | Tipos Administrador, Vendedor, Marketing (`marina`) e Personalizado (`carlos`), com eventos visíveis por usuário |
| Clientes (carteira e perfil) | Evento → **Clientes** | 9 clientes com CPF fictício, recompras entre a feira e o congresso; XLSX e PDF |
| Brindes | Evento → Promoções → **Brindes** | "A partir de R$ 500 ganha caneca", "a cada 3 caixas de luva, 1 espelho"; brindes avulsos com estoque |
| Integração ERP (simulada) | Admin → **Integração ERP** | ERP simulado em `sankhya_api.py` + `data/erp_mock.py`: testar conexão, sincronizar catálogo (traz 1 produto novo e 1 reajuste), tipos de negociação, fila ao vivo com 1 erro para reenviar |
| Consulta de CPF no checkout | Vendedor → pagamento | CPF de cliente da carteira ou só do ERP (Gustavo Ferraz) preenche o formulário |
| Movimentações do evento | Evento → Transações → **Movimentações de estoque** | Entradas, saídas e vendas com filtros, XLSX e PDF |
| Saída em lote / adicionar vários produtos ao evento | Estoque do evento / Biblioteca de produtos | — |
| Reserva de estoque no checkout | Dois vendedores com o mesmo último item | Aviso de conflito em tempo real |
| Gráficos (d3) | Dashboard do evento, Financeiro, Clientes, perfil do usuário | Vendas de 2 eventos em ~8 dias e ~45 dias atrás |
| Formas de pagamento | Checkout | PIX, crédito (até 12x), débito, dinheiro e faturado |
| Tipo de operação | Eventos | Evento (feira) e Congresso (encerrado) |
| Promoções redesenhadas, busca de produtos, trilha de navegação, PDF do pedido | Várias telas | — |

### Diferenças da demo em relação ao produto

- O ERP é simulado: nenhuma chamada sai da máquina. `DEMO_ERP_LATENCY` controla a espera (s) e `DEMO_ERP_FAIL_RATE` (0–1) força falhas aleatórias.
- CPF e endereço são opcionais no checkout; venda sem CPF vai ao ERP como consumidor final.
- Sem CRO e sem "Pix Inter".
- O cadastro manual de produto (`/admin/produtos/novo`) continua existindo, além da sincronização com o ERP.
- **Reiniciar** recria tudo (`seed/demo.py`), inclusive a fila e as filas do ERP simulado.
