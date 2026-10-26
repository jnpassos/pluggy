# Pluggy Data Fetcher - AGENTS.md

> **Project:** `/root/projetos/pluggy`  
> **Branch:** `dev` (main protected - explicit permission required)  
> **User:** Jhonatan Nunes dos Passos (conta pessoal)  
> **Last Updated:** 2026-10-26  
> **Version:** v1.1.0

---

## Project Overview

**Pluggy Data Fetcher** - CLI tool to fetch accounts, transactions, investments, loans, and credit card bills from Pluggy Open Finance API.

**Key Files:**
- `pluggy_fetch.py` - Main CLI script
- `items.json` - Local mapping: name → item_id (required for contas pessoais)
- `requirements.txt` - `requests>=2.31.0`, `python-dotenv`

---

## Critical Rules (From Pluggy Skills + Project Sessions)

### 1. Authentication & API Keys

**Rules from `pluggy-integration` + project sessions:**

| Rule | Implementation |
|------|----------------|
| **API Keys only on backend** | ✅ `pluggy_fetch.py` uses env var `PLUGGY_API_KEY` |
| **Token expires in 2h** | ✅ Must refresh via `POST /auth` with `clientId` + `clientSecret` |
| **Never hardcode secrets** | ✅ Uses `.env` + `os.getenv()` |
| **Conta pessoal → `/items` returns 401** | ✅ Workaround: `items.json` local mapping |

**Token Refresh Pattern (from session):**
```bash
# Generate new API key when 401/403 occurs
curl -X POST https://api.pluggy.ai/auth \
  -H "Content-Type: application/json" \
  -d '{"clientId": "$PLUGGY_CLIENT_ID", "clientSecret": "$PLUGGY_CLIENT_SECRET"}'
```

**Environment Variables Required:**
```bash
PLUGGY_CLIENT_ID=dc1c634f-ce00-451f-97e6-4efb67e09f79
PLUGGY_CLIENT_SECRET=ASnUUgkWuI3EB4-_KBsm_P3VI041bSPMxIbXdqlJqOc
PLUGGY_API_KEY=<jwt-token>  # Auto-refreshed, expires 2h
```

---

### 2. Items & Contas Pessoais

**CRITICAL: `/items` endpoint returns 401 for personal accounts**

```python
# pluggy_fetch.py - items.json workaround (lines 286-297)
def load_items_map(items_file: str = "items.json") -> dict:
    with open(items_file, encoding="utf-8") as f:
        items = json.load(f)
    return {
        item["name"].lower(): item["item_id"]
        for item in items
        if "name" in item and "item_id" in item
    }
```

**items.json Structure:**
```json
[
  {"name": "Nubank", "short_name": "nubank", "item_id": "2f5330c9-afbc-4241-9715-bbc936666222"},
  {"name": "Itaú", "short_name": "itau", "item_id": "758f2c27-7a63-4b38-8638-b338c5b333c0"},
  {"name": "Banco Inter", "short_name": "banco_inter", "item_id": "523b8a02-a3c8-4ac0-b9dc-7a89edf06f90"},
  {"name": "Santander", "short_name": "santander", "item_id": "a1f93bea-0789-47eb-92c1-ec2d2d768b96"}
]
```

**Usage:**
```bash
# Via CLI (resolves name → item_id)
python3 pluggy_fetch.py --item-name Nubank --only transactions

# All banks from items.json
python3 pluggy_fetch.py --all-transactions --from-date 2026-10-01 --to-date 2026-10-31
```

---

### 3. Transactions - API v2 (Cursor Pagination)

**FIXED in v1.1.0: Migrated from deprecated API v1**

| API Version | Endpoint | Pagination | Status |
|-------------|----------|------------|--------|
| v1 (deprecated) | `/transactions?itemId=...` | page/pageSize | ❌ HTTP 400 |
| v2 (current) | `/v2/transactions?accountId=...` | cursor (`next` URL) | ✅ Working |

**Implementation in `fetch_transactions_v2` (lines 122-175):**
```python
def fetch_transactions_v2(self, account_id: str, from_date=None, to_date=None, ...):
    # 1. Fetch first page
    params = {"accountId": account_id}
    data = self._get("/v2/transactions", params)
    
    # 2. Follow cursor pagination
    while True:
        results = data.get("results", [])
        all_results.extend(results)
        
        next_url = data.get("next")  # Full query string: ?accountId=...&after=...
        if not next_url:
            break
            
        url = f"{self.config.base_url}/v2/transactions{next_url}"
        resp = self.session.get(url)
        data = resp.json()
    
    # 3. Client-side date/type filtering (API v2 doesn't support server-side)
    if from_date or to_date or transaction_type:
        filtered = []
        for tx in all_results:
            tx_date = tx.get("date", "")[:10]  # YYYY-MM-DD
            if from_date and tx_date < from_date: continue
            if to_date and tx_date > to_date: continue
            if transaction_type and tx.get("type") != transaction_type: continue
            filtered.append(tx)
        return filtered
```

**Key Points:**
- Requires `accountId` (not `itemId`) - fetch accounts first
- Cursor pagination via `next` query string
- Server-side date/type filters NOT supported - client-side filtering required
- Page size default: 500

---

### 4. Credit Card Bills - Uses `accountId`

**FIXED in v1.1.0: `/bills` requires `accountId` of credit card account**

```python
# pluggy_fetch.py line 258
def fetch_bills(self, account_id: str) -> list:
    return self._get_all_pages("/bills", {"accountId": account_id})

# Called per credit card account (lines 523-542)
for account in all_data.get("accounts", []):
    if account.get("type") in ("CREDIT_CARD", "CREDIT"):
        bills = client.fetch_bills(account.get("id"))
```

---

### 5. Sync Strategy (from `pluggy-open-finance`)

| Responsibility | Who | How |
|----------------|-----|-----|
| **Connection sync** | Pluggy | Auto 24/12/8h (tier-based) |
| **Trigger updates** | User | Only explicit request |
| **Entity data sync** | You | Fetch all on `item/updated` webhook |
| **Transaction sync** | You | Use `transactions/*` webhook events |

**This project:** Manual fetch on demand (no webhooks implemented)

---

### 6. Pagination & Data Retrieval

| Endpoint | Pagination | Max Page Size |
|----------|------------|---------------|
| `/connectors` | Direct list or paginated | N/A |
| `/items` | Paginated (if Pro) | 500 |
| `/accounts` | Paginated | 500 |
| `/transactions` v1 | Paginated | 500 |
| `/transactions` v2 | Cursor (`next` URL) | 500 |
| `/investments` | Paginated | 500 |
| `/loans` | Paginated | 500 |
| `/bills` | Paginated | 500 |

**Pattern used in project (`_get_all_pages`, lines 54-72):**
```python
def _get_all_pages(self, endpoint: str, params: dict | None = None, page_size: int = 500) -> list:
    all_results = []
    page = 1
    while True:
        page_params = {**params, "page": page, "pageSize": page_size}
        data = self._get(endpoint, page_params)
        results = data.get("results", [])
        all_results.extend(results)
        if len(results) < page_size:
            break
        page += 1
    return all_results
```

---

## CLI Usage Reference

### Main Commands
```bash
# List connectors (banks)
python3 pluggy_fetch.py --only connectors

# List local items (from items.json)
python3 pluggy_fetch.py --only items

# Fetch accounts for specific bank
python3 pluggy_fetch.py --item-name Nubank --only accounts

# Fetch transactions with date filter
python3 pluggy_fetch.py --item-name Nubank --from-date 2026-10-01 --to-date 2026-10-31 --only transactions

# Fetch ALL data for bank
python3 pluggy_fetch.py --item-name Nubank --only all

# Fetch transactions from ALL banks in items.json (date filtered)
python3 pluggy_fetch.py --all-transactions --from-date 2026-10-01 --to-date 2026-10-31

# Save output to JSON
python3 pluggy_fetch.py --item-name Nubank --only transactions --output nubank_tx.json

# Filter by transaction type
python3 pluggy_fetch.py --item-name Nubank --type PIX --only transactions
```

### Arguments
| Arg | Description |
|-----|-------------|
| `--item-id` | Specific item UUID (skips items.json lookup) |
| `--item-name` | Bank name from items.json (Nubank, Itaú, etc.) |
| `--from-date` | Start date YYYY-MM-DD |
| `--to-date` | End date YYYY-MM-DD |
| `--type` | CREDIT, DEBIT, PIX, TED, DOC, BOLETO |
| `--only` | connectors, items, accounts, transactions, all |
| `--output` | Output JSON file |
| `--quiet` | Less output |
| `--all-transactions` | All banks from items.json (uses API v2) |

---

## Error Handling Patterns

### From Project Sessions
```python
# HTTP Errors
try:
    data = client.fetch_transactions_v2(account_id, from_date, to_date)
except requests.HTTPError as e:
    if e.response.status_code == 401:
        # Token expired - refresh and retry
        refresh_token()
    elif e.response.status_code == 403:
        # Invalid token - regenerate
        regenerate_token()
    elif e.response.status_code == 400:
        # Bad request - check params (accountId vs itemId)
        log_error(e.response.text)
    raise

# Network/Other Errors
except (requests.RequestException, ValueError, KeyError, OSError) as e:
    log_error(str(e))
```

### Common Error Codes (from `pluggy-integration`)
| Code | Meaning | Action |
|------|---------|--------|
| `INVALID_API_KEY` | Token expired/invalid | Refresh via `/auth` |
| `RATE_LIMIT_EXCEEDED` | Too many requests | Exponential backoff |
| `ITEM_NOT_FOUND` | Item deleted | Reconnect account |
| `CONNECTOR_UNAVAILABLE` | Bank API down | Wait and retry |
| `INVALID_CREDENTIALS` | Wrong password | Update via Connect Widget |

---

## Data Models (Key Fields)

### Account
```python
{
    "id": "uuid",
    "type": "BANK|CREDIT",
    "subtype": "CHECKING_ACCOUNT|SAVINGS_ACCOUNT|CREDIT_CARD",
    "name": "Nu Pagamentos S.A.",
    "balance": 72.0,
    "currencyCode": "BRL",
    "creditData": {  # Only for CREDIT_CARD
        "creditLimit": 4400,
        "availableCreditLimit": 3667.02,
        "balanceDueDate": "2026-10-13",
        "brand": "MASTERCARD"
    }
}
```

### Transaction
```python
{
    "id": "uuid",
    "description": "99",
    "descriptionRaw": "99",
    "amount": -8.97,
    "currencyCode": "BRL",
    "date": "2026-10-24T23:19:26.033Z",
    "type": "DEBIT|CREDIT",
    "category": "Taxi and ride-hailing",
    "categoryId": "19010000",
    "status": "POSTED|PENDING",
    "operationType": "PIX|PAGAMENTO|OUTROS",
    "accountId": "uuid",
    "merchant": {"cnae": "...", "cnpj": "...", "businessName": "..."}
}
```

### Investment
```python
{
    "id": "uuid",
    "name": "CDB - NU FINANCEIRA",
    "type": "FIXED_INCOME",
    "balance": 0,
    "currency": "BRL",
    "amountProfit": 0,
    "annualRate": 13.5,
    "dueDate": "2027-01-15"
}
```

### Bill (Credit Card)
```python
{
    "id": "uuid",
    "accountId": "uuid",
    "totalAmount": 734.42,
    "totalAmountCurrencyCode": "BRL",
    "dueDate": "2026-10-13T00:00:00.000Z",
    "billClosingDate": "2026-10-04T00:00:00.000Z",
    "minimumPaymentAmount": 202.46,
    "payments": [...]
}
```

---

## Logging & Debugging

### Log Directory
```
/root/projetos/pluggy/logs/
├── test_report_2026-10-26.md      # Initial test report
├── test_summary_2026-10-26.md     # Complete function test summary
├── nubank_test_*.log              # Individual Nubank tests
├── all_banks_test_*.log           # Multi-bank tests
├── test_transactions_*.log        # Transaction tests
├── test_bills_fixed_*.log         # Bills (after fix)
└── test_investments_loans_*.log   # Investments/loans
```

### Test Commands Used
```bash
# Test with tee to save log
export PLUGGY_API_KEY="<token>" && \
cd /root/projetos/pluggy && \
time python3 pluggy_fetch.py --item-name Nubank --from-date 2026-10-01 --to-date 2026-10-31 --only transactions 2>&1 | tee logs/test_$(date +%Y%m%d_%H%M%S).log
```

---

## Security Rules (From User AGENTS.md)

1. **Nunca ler segredos** (API keys, tokens, senhas)
2. **Jamais enviá-los à internet** (logs, prompts, PRs)
3. **Sempre usar variáveis de ambiente**
4. **Sempre perguntar antes de acessar arquivos com segredos**
5. **Nunca deletar permanentemente** dados (Gmail/Drive) - só archive/trash

---

## Project Rules

1. **Idioma padrão: Português** - Todas as respostas, docs, commits e logs em português

---

## Git Workflow

```bash
# Branch: dev (main protected)
git checkout dev

# Commit changes
git add pluggy_fetch.py
git commit -m "fix: description"

# Tag version
git tag -a v1.1.0 -m "v1.1.0: description"

# Push
git push origin dev && git push origin v1.1.0
```

---

## Test Results Summary (2026-10-26)

| Function | Status | Records | Time |
|----------|--------|---------|------|
| Connectors | ✅ | 229 | ~1.7s |
| Items (local) | ✅ | 4 banks | N/A |
| Accounts (Nubank) | ✅ | 2 | ~1.9s |
| Transactions v2 (all) | ✅ | 519 | ~2.9s |
| Transactions v2 (out/26) | ✅ | 47 | ~2.4s |
| Investments | ✅ | 7 | ~1.9s |
| Loans | ✅ | 0 | ~1.9s |
| Bills (credit) | ✅ | 12 | ~1.9s |
| All banks (out/26) | ✅ | 92 | ~3.8s |

**Performance:** ~25-180 tx/s depending on date range

---

## Known Limitations

1. **`/items` endpoint** - Returns 401 for personal accounts (by design)
2. **Investments show 0 balance** - CDBs appear redeemed/zero
3. **API Key rotation** - Manual refresh needed every 2h
4. **No webhooks** - Manual fetch only
5. **No enrichment** - Raw transaction data only

---

## Future Improvements

- [ ] Auto token refresh (check JWT `exp` claim)
- [ ] Retry with exponential backoff
- [ ] Webhook receiver for real-time sync
- [ ] Transaction enrichment API integration
- [ ] CSV/Parquet export options
- [ ] Investment performance calculations
- [ ] Recurring payments detection

---

## References

- [Pluggy Docs](https://docs.pluggy.ai)
- [Pluggy API Reference](https://docs.pluggy.ai/reference)
- [Connectors Coverage](https://docs.pluggy.ai/docs/connectors-coverage)
- [Webhooks](https://docs.pluggy.ai/docs/webhooks)
- [Sandbox](https://docs.pluggy.ai/docs/sandbox)
- [Error Codes](https://docs.pluggy.ai/docs/errors-codes)

---

*Generated from project sessions, Pluggy skills (pluggy-integration, pluggy-open-finance, pluggy-payments), and v1.1.0 fixes.*