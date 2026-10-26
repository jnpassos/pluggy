# Test Summary - All Functions (2026-10-26)

## Git Status
- **Branch:** dev
- **Commit:** e85714e - "fix: migrate from deprecated API v1 to v2 for transactions"
- **Tag:** v1.1.0
- **Pushed to:** origin/dev, origin/v1.1.0

---

## Functions Tested

| Função | Endpoint | Status | Detalhes |
|--------|----------|--------|----------|
| **Conectores** | `GET /connectors` | ✅ PASS | 229 conectores listados |
| **Itens** | `GET /items` | ❌ FAIL | HTTP 401 Unauthorized (scope/permission issue) |
| **Contas** | `GET /accounts?itemId=...` | ✅ PASS | 2 contas Nubank (checking + credit) |
| **Transações (v2)** | `GET /v2/transactions?accountId=...` | ✅ PASS | 519 txs (all), 47 txs (out/2026) |
| **Investimentos** | `GET /investments?itemId=...` | ✅ PASS | 7 CDBs (valor 0 - possivelmente resgatados) |
| **Empréstimos** | `GET /loans?itemId=...` | ✅ PASS | 0 empréstimos |
| **Faturas** | `GET /bills?accountId=...` | ✅ PASS | 12 faturas cartão platinum |
| **Todas transações** | Multi-banco via items.json | ✅ PASS | 92 txs de 4 bancos (out/2026) |

---

## Test Commands & Results

### 1. Conectores
```bash
python3 pluggy_fetch.py --item-name Nubank --only connectors
```
**Resultado:** 229 conectores | Tempo: ~1.7s

### 2. Contas (Nubank)
```bash
python3 pluggy_fetch.py --item-name Nubank --only accounts
```
**Resultado:** 2 contas | Tempo: ~1.9s
- Nu Pagamentos S.A. (checking) - R$ 72,00
- platinum (credit card) - R$ 732,98

### 3. Transações - Todos os dados (Nubank)
```bash
python3 pluggy_fetch.py --item-name Nubank --only transactions
```
**Resultado:** 519 transações | Tempo: ~2.9s
- Créditos: R$ 8.502,85
- Débitos: R$ -7.839,25
- Líquido: R$ 663,60

### 4. Transações - Outubro 2026 (Nubank)
```bash
python3 pluggy_fetch.py --item-name Nubank --from-date 2026-10-01 --to-date 2026-10-31 --only transactions
```
**Resultado:** 47 transações | Tempo: ~2.4s
- Créditos: R$ 699,81
- Débitos: R$ -752,23
- Líquido: R$ -52,42

### 5. Investimentos (Nubank)
```python
client.fetch_investments(item_id)
```
**Resultado:** 7 CDBs (todos com valor 0)

### 6. Empréstimos (Nubank)
```python
client.fetch_loans(item_id)
```
**Resultado:** 0 empréstimos

### 7. Faturas Cartão (Nubank Platinum)
```python
client.fetch_bills(account_id)  # accountId do cartão de crédito
```
**Resultado:** 12 faturas | Tempo: ~1.9s
- Última: Out/2026 - R$ 734,42 (PAGAMENTO_INTEGRAL)
- Histórico: Nov/2025 a Out/2026

### 8. Todas Transações - Todos os Bancos (Out/2026)
```bash
python3 pluggy_fetch.py --all-transactions --from-date 2026-10-01 --to-date 2026-10-31
```
**Resultado:** 92 transações de 4 bancos | Tempo: ~3.8s

| Banco | Transações | Créditos | Débitos | Líquido |
|-------|------------|----------|---------|---------|
| Banco Inter | 2 | R$ 1.829,00 | R$ -1.829,00 | R$ 0,00 |
| Itaú | 6 | R$ 1.885,81 | R$ -1.928,62 | R$ -42,81 |
| **Nubank** | **83** | **R$ 2.599,71** | **R$ -2.580,13** | **R$ 19,58** |
| Santander | 1 | R$ 0,13 | R$ 0,00 | R$ 0,13 |

---

## Issues Found & Fixed

| Issue | Status | Fix |
|-------|--------|-----|
| API Key expiração (2h) | ✅ Fixed | Regenerate via `POST /auth` with Client ID/Secret |
| API v1 deprecada - `itemId` inválido | ✅ Fixed | Migrado para API v2 com `accountId` |
| `fetch_bills` usava `itemId` | ✅ Fixed | Alterado para `accountId` (cartão de crédito) |
| Filtro de data client-side | ✅ Working | Implementado em `fetch_transactions_v2` |
| Paginação cursor v2 | ✅ Working | Loop com `next` URL |

---

## Logs Generated

```
/root/projetos/pluggy/logs/
├── nubank_test_20261026_022051.log           # Nubank transações out/2026
├── all_banks_test_20261026_022102.log        # Todos bancos transações out/2026
├── test_report_2026-10-26.md                 # Relatório detalhado inicial
├── full_test_all_*.log                       # Tentativa --only all (falhou em items)
├── test_accounts_*.log                       # Teste contas
├── test_transactions_*.log                   # Teste transações (todas)
├── test_transactions_oct_*.log               # Teste transações out/2026
├── test_investments_loans_bills_*.log        # Invest/loans/bills (bills falhou)
├── test_bills_fixed_*.log                    # Bills corrigido (12 faturas)
├── test_working_functions_*.log              # Testes individuais
├── test_individual_functions_*.log           # Connectors, accounts, transactions
├── test_investments_loans_*.log              # Investimentos e empréstimos
├── test_all_transactions_*.log               # Todos bancos out/2026
└── test_summary_2026-10-26.md                # Este arquivo
```

---

## Known Limitations

1. **`/items` endpoint returns 401** - The API key may lack the required scope for listing items. Workaround: Use local `items.json` mapping (already implemented via `--item-name` and `--all-transactions`).

2. **Investments show 0 balance** - The 7 CDBs returned have amount=0, possibly because they were redeemed or the data structure differs.

3. **API Key rotation needed** - Keys expire in 2 hours. Consider implementing automatic token refresh in production.

---

## Performance Metrics

| Operação | Tempo Real | Transações | Tx/seg |
|----------|------------|------------|--------|
| Nubank transações (out/2026) | 2.4s | 47 | 19.6 |
| Nubank transações (todas) | 2.9s | 519 | 179 |
| Todos bancos (out/2026) | 3.8s | 92 | 24.2 |
| Contas | 1.9s | N/A | N/A |
| Faturas (12) | 1.9s | N/A | N/A |

---

## Next Steps

1. [ ] Implement automatic API key refresh (check JWT exp claim)
2. [ ] Add retry with exponential backoff for rate limits
3. [ ] Investigate `/items` 401 - check Pluggy dashboard for scopes
4. [ ] Add caching for connectors/accounts to reduce API calls
5. [ ] Add output format options (CSV, JSON, Parquet)