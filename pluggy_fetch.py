#!/usr/bin/env python3
"""
Pluggy Data Fetcher - Lista contas, bancos, cartões de crédito e transações.
Todas as credenciais via variáveis de ambiente (.env ou export).
"""

import argparse
import json
import os
import sys
from dataclasses import dataclass

import requests
from dotenv import load_dotenv


@dataclass
class Config:
    api_key: str
    base_url: str = "https://api.pluggy.ai"
    timeout: int = 30

    @classmethod
    def from_env(cls) -> "Config":
        # Carrega .env automaticamente se existir
        load_dotenv()
        api_key = os.getenv("PLUGGY_API_KEY")
        if not api_key:
            raise ValueError(
                "PLUGGY_API_KEY não definida. Defina no .env ou: export PLUGGY_API_KEY='sua_key'"
            )
        return cls(api_key=api_key)


class PluggyClient:
    def __init__(self, config: Config):
        self.config = config
        self.session = requests.Session()
        self.session.headers.update(
            {
                "X-API-KEY": config.api_key,
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )
        self.session.timeout = config.timeout

    def _get(self, endpoint: str, params: dict | None = None) -> dict:
        url = f"{self.config.base_url}{endpoint}"
        resp = self.session.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

    def _get_all_pages(
        self, endpoint: str, params: dict | None = None, page_size: int = 500
    ) -> list:
        """Busca todos os resultados paginados."""
        all_results = []
        page = 1
        params = params or {}

        while True:
            page_params = {**params, "page": page, "pageSize": page_size}
            data = self._get(endpoint, page_params)
            results = data.get("results", [])
            all_results.extend(results)

            if len(results) < page_size:
                break
            page += 1

        return all_results

    # ===== CONECTORES / BANCOS =====
    def fetch_connectors(self) -> list:
        """Lista todos os conectores/bancos disponíveis."""
        # /connectors pode retornar lista direta ou paginado
        data = self._get("/connectors")
        if isinstance(data, dict) and "results" in data:
            return data["results"]
        return data if isinstance(data, list) else []

    # ===== ITENS (CONEXÕES) =====
    def fetch_items(self) -> list:
        """Lista todos os itens (conexões do usuário)."""
        return self._get_all_pages("/items")

    def fetch_item(self, item_id: str) -> dict:
        """Busca detalhes de um item específico."""
        return self._get(f"/items/{item_id}")

    # ===== CONTAS =====
    def fetch_accounts(self, item_id: str) -> list:
        """Lista contas de um item."""
        return self._get_all_pages("/accounts", {"itemId": item_id})

    def fetch_account(self, account_id: str) -> dict:
        """Busca detalhes de uma conta específica."""
        return self._get(f"/accounts/{account_id}")

    # ===== TRANSAÇÕES =====
    def fetch_transactions(
        self,
        item_id: str,
        from_date: str | None = None,
        to_date: str | None = None,
        transaction_type: str | None = None,
    ) -> list:
        """Busca transações de um item com filtros opcionais (API v1 - deprecated)."""
        params = {"itemId": item_id}
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date
        if transaction_type:
            params["type"] = transaction_type
        return self._get_all_pages("/transactions", params)

    def fetch_transaction(self, transaction_id: str) -> dict:
        return self._get(f"/transactions/{transaction_id}")

    def fetch_transactions_v2(
        self,
        account_id: str,
        from_date: str | None = None,
        to_date: str | None = None,
        transaction_type: str | None = None,
        page_size: int = 500,
    ) -> list:
        """Busca transações via API v2 (cursor pagination) para uma conta específica.

        Note: API v2 não suporta filtros de data (from/to) nem type -
        filtra client-side se necessário.
        O campo 'next' retorna uma query string completa
        (ex: ?accountId=...&after=...).
        """
        all_results = []
        next_url = None

        while True:
            if next_url:
                # next_url já contém a query string completa
                url = f"{self.config.base_url}/v2/transactions{next_url}"
                resp = self.session.get(url)
                resp.raise_for_status()
                data = resp.json()
            else:
                params = {"accountId": account_id}
                data = self._get("/v2/transactions", params)

            results = data.get("results", [])
            all_results.extend(results)

            # Check for next cursor (vem como query string: ?accountId=...&after=...)
            next_url = data.get("next")
            if not next_url:
                break

        # Filtro client-side para datas e tipo se necessário
        if from_date or to_date or transaction_type:
            filtered = []
            for tx in all_results:
                tx_date = tx.get("date", "")[:10]  # YYYY-MM-DD
                if from_date and tx_date < from_date:
                    continue
                if to_date and tx_date > to_date:
                    continue
                if transaction_type:
                    tx_type = tx.get("type", "")
                    if tx_type != transaction_type:
                        continue
                filtered.append(tx)
            return filtered

        return all_results

    def fetch_all_transactions_all_items(
        self,
        from_date: str | None = None,
        to_date: str | None = None,
        transaction_type: str | None = None,
        quiet: bool = False,
    ) -> dict:
        """Busca transações de TODOS os itens (bancos) em items.json."""
        # Carrega items.json
        items_map = load_items_map()

        all_transactions = []
        all_accounts = {}
        successful_banks = 0

        for name, item_id in items_map.items():
            if not quiet:
                print(f"\n🏦 Buscando contas do {name} ({item_id})...")

            try:
                # Busca contas do item
                accounts = self.fetch_accounts(item_id)
                all_accounts[name] = accounts
            except requests.HTTPError as e:
                if not quiet:
                    print(f"  ⚠️  Erro ao buscar contas: HTTP {e.response.status_code}")
                continue
            except (requests.RequestException, ValueError, KeyError, OSError) as e:
                if not quiet:
                    print(f"  ⚠️  Erro ao buscar contas: {e}")
                continue

            # Para cada conta, busca transações
            for account in accounts:
                account_id = account.get("id")
                account_name = account.get("name", "Unknown")
                if not quiet:
                    print(f"  💳 Buscando transações: {account_name} ({account_id})")

                try:
                    transactions = self.fetch_transactions_v2(
                        account_id,
                        from_date=from_date,
                        to_date=to_date,
                        transaction_type=transaction_type,
                    )
                except requests.HTTPError as e:
                    if not quiet:
                        print(f"    ⚠️  Erro HTTP {e.response.status_code}")
                    continue
                except (requests.RequestException, ValueError, KeyError, OSError) as e:
                    if not quiet:
                        print(f"    ⚠️  Erro: {e}")
                    continue

                # Adiciona metadados às transações
                for tx in transactions:
                    tx["_bank_name"] = name
                    tx["_account_id"] = account_id
                    tx["_account_name"] = account_name

                all_transactions.extend(transactions)

            successful_banks += 1

        return {
            "transactions": all_transactions,
            "accounts_by_bank": all_accounts,
            "total_transactions": len(all_transactions),
            "total_banks": successful_banks,
        }

    # ===== INVESTIMENTOS =====
    def fetch_investments(self, item_id: str) -> list:
        return self._get_all_pages("/investments", {"itemId": item_id})

    # ===== EMPRÉSTIMOS =====
    def fetch_loans(self, item_id: str) -> list:
        return self._get_all_pages("/loans", {"itemId": item_id})

    # ===== FATURAS CARTÃO =====
    def fetch_bills(self, item_id: str) -> list:
        return self._get_all_pages("/bills", {"itemId": item_id})


def print_json(data, indent=2):
    print(json.dumps(data, indent=indent, ensure_ascii=False, default=str))


def print_summary(label: str, data: list, key_fields: list | None = None):
    print(f"\n{'=' * 60}")
    print(f"{label}: {len(data)} itens")
    print(f"{'=' * 60}")
    if not data:
        print("  (vazio)")
        return

    if key_fields:
        for item in data[:10]:  # mostra só primeiros 10
            parts = [f"{k}={item.get(k, 'N/A')}" for k in key_fields]
            print(f"  - {' | '.join(parts)}")
        if len(data) > 10:
            print(f"  ... e mais {len(data) - 10} itens")
    else:
        print_json(data[:3])
        if len(data) > 3:
            print(f"  ... (total: {len(data)})")


def load_items_map(items_file: str = "items.json") -> dict:
    """Carrega mapeamento name -> item_id do arquivo items.json."""
    try:
        with open(items_file, encoding="utf-8") as f:
            items = json.load(f)
        return {
            item["name"].lower(): item["item_id"]
            for item in items
            if "name" in item and "item_id" in item
        }
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return {}


def main():
    parser = argparse.ArgumentParser(description="Pluggy Data Fetcher")
    parser.add_argument("--item-id", help="Item ID específico (pula listagem de itens)")
    parser.add_argument("--item-name", help="Nome do banco (ex: Nubank, Itaú) - usa items.json")
    parser.add_argument("--from-date", help="Data inicial (YYYY-MM-DD)")
    parser.add_argument("--to-date", help="Data final (YYYY-MM-DD)")
    parser.add_argument(
        "--type",
        choices=["CREDIT", "DEBIT", "PIX", "TED", "DOC", "BOLETO"],
        help="Filtrar tipo de transação",
    )
    parser.add_argument(
        "--only",
        choices=["connectors", "items", "accounts", "transactions", "all"],
        default="all",
        help="O que buscar",
    )
    parser.add_argument("--output", help="Arquivo JSON de saída")
    parser.add_argument("--quiet", "-q", action="store_true", help="Menos output")
    parser.add_argument(
        "--all-transactions",
        action="store_true",
        help="Busca transações de TODOS os bancos em items.json (usa API v2)",
    )
    args = parser.parse_args()

    # Carrega mapeamento de nomes
    items_map = load_items_map()

    # Resolve --item-name para item_id
    if args.item_name:
        name_lower = args.item_name.lower()
        if name_lower in items_map:
            args.item_id = items_map[name_lower]
            if not args.quiet:
                print(f"📌 Item resolvido: {args.item_name} -> {args.item_id}")
        else:
            available = ", ".join(sorted(items_map.keys()))
            print(f"❌ Nome '{args.item_name}' não encontrado em items.json", file=sys.stderr)
            print(f"   Disponíveis: {available}", file=sys.stderr)
            sys.exit(1)

    try:
        config = Config.from_env()
    except ValueError as e:
        print(f"❌ Erro: {e}", file=sys.stderr)
        sys.exit(1)

    client = PluggyClient(config)
    all_data = {}

    # Handle --all-transactions FIRST (doesn't need target_item_id)
    if args.all_transactions:
        if not args.quiet:
            print("\n🌍 Buscando transações de TODOS os bancos (items.json)...")
        result = client.fetch_all_transactions_all_items(
            from_date=args.from_date,
            to_date=args.to_date,
            transaction_type=args.type,
        )
        all_data["transactions_all"] = result
        if not args.quiet:
            total_tx = result["total_transactions"]
            total_banks = result["total_banks"]
            print(f"\n✅ Total: {total_tx} transações de {total_banks} bancos")
            # Summary by bank
            for bank_name, accounts in result["accounts_by_bank"].items():
                bank_txs = [t for t in result["transactions"] if t.get("_bank_name") == bank_name]
                total_credit = sum(t.get("amount", 0) for t in bank_txs if t.get("amount", 0) > 0)
                total_debit = sum(t.get("amount", 0) for t in bank_txs if t.get("amount", 0) < 0)
                net = total_credit + total_debit
                print(
                    f"  {bank_name}: {len(bank_txs)} txs | "
                    f"Créditos: {total_credit:.2f} | Débitos: {total_debit:.2f} | "
                    f"Líquido: {net:.2f}"
                )

        # Save and exit
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                json.dump(all_data, f, indent=2, ensure_ascii=False, default=str)
            if not args.quiet:
                print(f"\n💾 Dados salvos em: {args.output}")
        if not args.quiet:
            print("\n✅ Concluído!")
        sys.exit(0)

    try:
        # 1. CONECTORES / BANCOS
        if args.only in ("connectors", "all"):
            if not args.quiet:
                print("\n🔌 Buscando conectores/bancos...")
            connectors = client.fetch_connectors()
            all_data["connectors"] = connectors
            if not args.quiet:
                print_summary("Conectores", connectors, ["id", "name", "type", "country"])

        # 2. ITENS
        if args.only in ("items", "all"):
            if not args.quiet:
                print("\n📦 Buscando itens (conexões)...")
            items = client.fetch_items()
            all_data["items"] = items
            if not args.quiet:
                print_summary("Itens", items, ["id", "name", "connectorId", "status", "updatedAt"])

        # Se item-id não foi passado, usa o primeiro item disponível
        target_item_id = args.item_id
        if not target_item_id and all_data.get("items"):
            target_item_id = all_data["items"][0]["id"]
            if not args.quiet:
                print(f"\n📌 Usando primeiro item: {target_item_id}")

        if target_item_id:
            # 3. CONTAS
            if args.only in ("accounts", "all"):
                if not args.quiet:
                    print(f"\n🏦 Buscando contas do item {target_item_id}...")
                accounts = client.fetch_accounts(target_item_id)
                all_data["accounts"] = accounts
                if not args.quiet:
                    print_summary(
                        "Contas", accounts, ["id", "name", "type", "subtype", "balance", "currency"]
                    )

            # Separar contas por tipo
            credit_accounts = [
                a
                for a in all_data.get("accounts", [])
                if a.get("type") in ("CREDIT_CARD", "CREDIT")
            ]
            _ = [
                a for a in all_data.get("accounts", []) if a.get("type") in ("CHECKING", "SAVINGS")
            ]

            if not args.quiet and credit_accounts:
                print_summary(
                    "Cartões de Crédito",
                    credit_accounts,
                    ["id", "name", "balance", "currency", "creditLimit"],
                )

            # 4. TRANSAÇÕES
            if args.only in ("transactions", "all"):
                if not args.quiet:
                    print(f"\n💳 Buscando transações do item {target_item_id}...")
                # Busca contas primeiro para usar API v2
                accounts = client.fetch_accounts(target_item_id)
                all_transactions = []
                for account in accounts:
                    account_id = account.get("id")
                    account_name = account.get("name", "Unknown")
                    if not args.quiet:
                        print(f"  💳 Buscando transações: {account_name} ({account_id})")
                    try:
                        transactions = client.fetch_transactions_v2(
                            account_id,
                            from_date=args.from_date,
                            to_date=args.to_date,
                            transaction_type=args.type,
                        )
                    except requests.HTTPError as e:
                        if not args.quiet:
                            print(f"    ⚠️  Erro HTTP {e.response.status_code}")
                        continue
                    except (requests.RequestException, ValueError, KeyError, OSError) as e:
                        if not args.quiet:
                            print(f"    ⚠️  Erro: {e}")
                        continue
                    
                    # Adiciona metadados às transações
                    for tx in transactions:
                        tx["_account_id"] = account_id
                        tx["_account_name"] = account_name
                    
                    all_transactions.extend(transactions)
                
                all_data["transactions"] = all_transactions
                if not args.quiet:
                    print_summary(
                        "Transações",
                        transactions,
                        ["id", "description", "amount", "currency", "date", "type"],
                    )

                # Resumo financeiro
                if transactions and not args.quiet:
                    total_credit = sum(
                        t.get("amount", 0) for t in transactions if t.get("amount", 0) > 0
                    )
                    total_debit = sum(
                        t.get("amount", 0) for t in transactions if t.get("amount", 0) < 0
                    )
                    net = total_credit + total_debit
                    print(
                        f"\n💰 Resumo: Créditos: {total_credit:.2f} | "
                        f"Débitos: {total_debit:.2f} | Líquido: {net:.2f}"
                    )

            # 5. INVESTIMENTOS (extra)
            if args.only == "all":
                if not args.quiet:
                    print("\n📈 Buscando investimentos...")
                investments = client.fetch_investments(target_item_id)
                all_data["investments"] = investments
                if not args.quiet:
                    print_summary(
                        "Investimentos", investments, ["id", "name", "type", "amount", "currency"]
                    )

            # 6. EMPRÉSTIMOS (extra)
            if args.only == "all":
                if not args.quiet:
                    print("\n🏦 Buscando empréstimos...")
                loans = client.fetch_loans(target_item_id)
                all_data["loans"] = loans
                if not args.quiet:
                    print_summary(
                        "Empréstimos",
                        loans,
                        ["id", "name", "type", "outstandingBalance", "currency"],
                    )

            # 7. FATURAS CARTÃO (extra)
            if args.only == "all":
                if not args.quiet:
                    print("\n🧾 Buscando faturas cartão...")
                bills = client.fetch_bills(target_item_id)
                all_data["bills"] = bills
                if not args.quiet:
                    print_summary(
                        "Faturas",
                        bills,
                        ["id", "accountId", "totalAmount", "currency", "dueDate", "status"],
                    )

    except requests.HTTPError as e:
        print(f"❌ Erro HTTP {e.response.status_code}: {e.response.text}", file=sys.stderr)
        sys.exit(1)
    except (requests.RequestException, ValueError, KeyError, OSError) as e:
        print(f"❌ Erro: {e}", file=sys.stderr)
        sys.exit(1)

    # Salva arquivo se solicitado
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(all_data, f, indent=2, ensure_ascii=False, default=str)
        if not args.quiet:
            print(f"\n💾 Dados salvos em: {args.output}")

    if not args.quiet:
        print("\n✅ Concluído!")


if __name__ == "__main__":
    main()
