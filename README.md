# Pluggy Data Fetcher

Python script para buscar dados do Pluggy Open Finance API:
- Conectores/bancos disponíveis
- Itens (conexões do usuário)
- Contas (corrente, poupança, cartão de crédito)
- Transações (com filtros de data e tipo)
- Investimentos
- Empréstimos
- Faturas de cartão de crédito

## Instalação

```bash
# Com uv (recomendado) - cria venv e instala deps automaticamente
uv sync

# Ou para rodar direto sem ativar venv
uv run python pluggy_fetch.py --help
```

## Configuração

```bash
export PLUGGY_API_KEY="sua_api_key_aqui"
```

## Uso

```bash
# Com uv (recomendado) - roda direto, gerencia venv automaticamente
uv run python pluggy_fetch.py

# Busca de um item específico
uv run python pluggy_fetch.py --item-id 2f5330c9-afbc-4241-9715-bbc936666222

# Apenas contas
uv run python pluggy_fetch.py --only accounts

# Transações de 2024
uv run python pluggy_fetch.py --only transactions --from-date 2024-01-01 --to-date 2024-12-31

# Salva em JSON
uv run python pluggy_fetch.py --output dados.json

# Modo silencioso
uv run python pluggy_fetch.py -q

# Lint e formatação
uv run ruff check pluggy_fetch.py
uv run ruff format pluggy_fetch.py
```

## Argumentos

| Argumento | Descrição |
|-----------|-----------|
| `--item-id` | Item ID específico (pula listagem de itens) |
| `--from-date` | Data inicial (YYYY-MM-DD) |
| `--to-date` | Data final (YYYY-MM-DD) |
| `--type` | Filtrar tipo: CREDIT, DEBIT, PIX, TED, DOC, BOLETO |
| `--only` | O que buscar: connectors, items, accounts, transactions, all |
| `--output` | Arquivo JSON de saída |
| `-q, --quiet` | Menos output |

## Itens configurados

O arquivo `items.json` contém mapeamento de item_ids para nomes dos bancos:

```json
[
  {"item_id": "2f5330c9-afbc-4241-9715-bbc936666222", "name": "Nubank"},
  {"item_id": "758f2c27-7a63-4b38-8638-b338c5b333c0", "name": "Itaú"},
  {"item_id": "a1f93bea-0789-47eb-92c1-ec2d2d768b96", "name": "Santander"},
  {"item_id": "523b8a02-a3c8-4ac0-b9dc-7a89edf06f90", "name": "Banco Inter"}
]
```

## Qualidade de código

```bash
# Lint com ruff
ruff check pluggy_fetch.py

# Formatação
ruff format pluggy_fetch.py
```

## Licença

MIT