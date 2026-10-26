#!/usr/bin/env python3
"""
Pluggy Data Fetcher - Lista contas, bancos, cartões de crédito e transações.
Todas as credenciais via variáveis de ambiente (.env ou export).
"""

import argparse
import base64
import csv
import json
import os
import random
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv


@dataclass
class Config:
    api_key: str
    base_url: str = "https://api.pluggy.ai"
    timeout: int = 30
    max_retries: int = 3
    backoff_factor: float = 1.0
    cache_dir: str = ".cache"

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


class TokenManager:
    """Gerencia token JWT com renovação baseada no claim 'exp'."""

    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self._api_key: str | None = None
        self._expires_at: float = 0

    def _decode_jwt_payload(self, token: str) -> dict:
        """Decodifica payload do JWT (sem verificação de assinatura)."""
        try:
            payload_b64 = token.split(".")[1]
            # Padding para base64url
            padding = "=" * (-len(payload_b64) % 4)
            payload_json = base64.urlsafe_b64decode(payload_b64 + padding)
            return json.loads(payload_json)
        except Exception:
            return {}

    def get_api_key(self) -> str:
        """Retorna API key válida, renovando se necessário (5 min antes do exp)."""
        buffer = 5 * 60  # 5 minutos
        if not self._api_key or time.time() > self._expires_at - buffer:
            self._refresh_token()
        return self._api_key

    def _refresh_token(self) -> None:
        """Renova token via endpoint /auth e extrai exp do JWT."""
        resp = requests.post(
            "https://api.pluggy.ai/auth",
            json={"clientId": self.client_id, "clientSecret": self.client_secret},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        self._api_key = data["apiKey"]

        # Extrai expiração real do JWT
        payload = self._decode_jwt_payload(self._api_key)
        exp = payload.get("exp")
        if exp:
            self._expires_at = exp
        else:
            # Fallback: 2h padrão
            self._expires_at = time.time() + 7200


class CacheManager:
    """Cache simples em arquivo para conectores e contas."""

    def __init__(self, cache_dir: str = ".cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(exist_ok=True)

    def _cache_path(self, key: str) -> Path:
        safe_key = key.replace("/", "_").replace(":", "_")
        return self.cache_dir / f"{safe_key}.json"

    def get(self, key: str, max_age_seconds: int = 3600) -> Any | None:
        """Retorna valor do cache se não expirado."""
        path = self._cache_path(key)
        if not path.exists():
            return None
        try:
            stat = path.stat()
            if time.time() - stat.st_mtime > max_age_seconds:
                return None
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    def set(self, key: str, value: Any) -> None:
        """Salva valor no cache."""
        path = self._cache_path(key)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(value, f, ensure_ascii=False, default=str)
        except OSError:
            pass  # Falha silenciosa no cache


class PluggyClient:
    def __init__(self, config: Config):
        self.config = config
        self.token_manager = TokenManager(
            os.getenv("PLUGGY_CLIENT_ID", ""),
            os.getenv("PLUGGY_CLIENT_SECRET", ""),
        )
        self.cache = CacheManager(config.cache_dir)
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )
        self.session.timeout = config.timeout

    def _get_headers(self) -> dict:
        """Retorna headers com API key atualizada."""
        return {"X-API-KEY": self.token_manager.get_api_key()}

    def _request_with_retry(
        self, method: str, url: str, params: dict | None = None, **kwargs
    ) -> requests.Response:
        """Faz requisição com retry exponencial + jitter para 429, 5xx e auto-refresh em 401."""
        last_exception = None

        for attempt in range(self.config.max_retries + 1):
            try:
                headers = {**self._get_headers(), **kwargs.pop("headers", {})}
                resp = self.session.request(
                    method, url, params=params, headers=headers, **kwargs
                )

                # Sucesso
                if resp.status_code < 400:
                    return resp

                # Rate limit (429) - respeita Retry-After
                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", "1"))
                    if attempt < self.config.max_retries:
                        wait = retry_after * (2**attempt) * self.config.backoff_factor
                        wait += random.uniform(0, 0.5)  # jitter
                        time.sleep(wait)
                        continue

                # 401 - token pode ter expirado, forçar refresh e tentar UMA vez
                if resp.status_code == 401 and attempt == 0:
                    self.token_manager._refresh_token()
                    continue

                # Server errors (5xx) - retry com backoff + jitter
                if 500 <= resp.status_code < 600:
                    if attempt < self.config.max_retries:
                        wait = self.config.backoff_factor * (2**attempt) + random.uniform(0, 1)
                        time.sleep(wait)
                        continue

                # Outros erros HTTP - não faz retry
                resp.raise_for_status()

            except requests.RequestException as e:
                last_exception = e
                if attempt < self.config.max_retries:
                    wait = self.config.backoff_factor * (2**attempt) + random.uniform(0, 1)
                    time.sleep(wait)
                    continue
                raise

        if last_exception:
            raise last_exception
        raise requests.RequestException("Max retries exceeded")

    def _get(self, endpoint: str, params: dict | None = None, use_cache: bool = False, cache_ttl: int = 3600) -> dict:
        """GET com cache opcional e retry."""
        cache_key = f"GET_{endpoint}_{json.dumps(params, sort_keys=True)}"

        if use_cache:
            cached = self.cache.get(cache_key, cache_ttl)
            if cached is not None:
                return cached

        url = f"{self.config.base_url}{endpoint}"
        resp = self._request_with_retry("GET", url, params=params)
        data = resp.json()

        if use_cache:
            self.cache.set(cache_key, data)

        return data

    def _get_all_pages(
        self, endpoint: str, params: dict | None = None, page_size: int = 500, use_cache: bool = False
    ) -> list:
        """Busca todos os resultados paginados."""
        all_results = []
        page = 1
        params = params or {}

        while True:
            page_params = {**params, "page": page, "pageSize": page_size}
            data = self._get(endpoint, page_params, use_cache=use_cache)
            results = data.get("results", [])
            all_results.extend(results)

            if len(results) < page_size:
                break
            page += 1

        return all_results

    # ===== CONECTORES / BANCOS =====
    def fetch_connectors(self, use_cache: bool = True) -> list:
        """Lista todos os conectores/bancos disponíveis."""
        data = self._get("/connectors", use_cache=use_cache, cache_ttl=86400)  # 24h cache
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
    def fetch_accounts(self, item_id: str, use_cache: bool = True) -> list:
        """Lista contas de um item."""
        return self._get_all_pages("/accounts", {"itemId": item_id}, use_cache=use_cache)

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
        Filtra por 'type' (DEBIT/CREDIT) OU 'operationType' (PIX, TED, DOC, BOLETO, etc).
        """
        all_results = []
        next_url = None

        while True:
            if next_url:
                url = f"{self.config.base_url}/v2/transactions{next_url}"
                resp = self._request_with_retry("GET", url)
                data = resp.json()
            else:
                params = {"accountId": account_id}
                data = self._get("/v2/transactions", params)

            results = data.get("results", [])
            all_results.extend(results)

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
                    tx_op_type = tx.get("operationType", "")
                    # Aceita match em type OU operationType
                    if tx_type != transaction_type and tx_op_type != transaction_type:
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
        items_map = load_items_map()

        all_transactions = []
        all_accounts = {}
        successful_banks = 0

        for name, item_id in items_map.items():
            if not quiet:
                print(f"\n🏦 Buscando contas do {name} ({item_id})...")

            try:
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
    def fetch_bills(self, account_id: str) -> list:
        return self._get_all_pages("/bills", {"accountId": account_id})


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
        for item in data[:10]:
            parts = [f"{k}={item.get(k, 'N/A')}" for k in key_fields]
            print(f"  - {' | '.join(parts)}")
        if len(data) > 10:
            print(f"  ... e mais {len(data) - 10} itens")
    else:
        print_json(data[:3])
        if len(data) > 3:
            print(f"  ... (total: {len(data)})")


def write_output(data: dict, output_file: str, format: str = "json"):
    """Escreve saída em JSON, CSV ou Parquet."""
    if format == "json":
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    elif format == "csv":
        # Flatten para CSV - usa transações se disponível
        transactions = data.get("transactions") or data.get("transactions_all", {}).get("transactions", [])
        if transactions:
            with open(output_file, "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=transactions[0].keys())
                writer.writeheader()
                writer.writerows(transactions)
        else:
            # Fallback: JSON
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    elif format == "parquet":
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq

            transactions = data.get("transactions") or data.get("transactions_all", {}).get("transactions", [])
            if transactions:
                # Normalizar dados aninhados (paymentData, merchant, etc.)
                flat_transactions = []
                for tx in transactions:
                    flat_tx = {}
                    for k, v in tx.items():
                        if isinstance(v, (dict, list)):
                            flat_tx[k] = json.dumps(v, ensure_ascii=False)
                        else:
                            flat_tx[k] = v
                    flat_transactions.append(flat_tx)

                table = pa.Table.from_pylist(flat_transactions)
                pq.write_table(table, output_file)
            else:
                # Fallback
                with open(output_file.replace(".parquet", ".json"), "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False, default=str)
        except ImportError:
            print("⚠️  pyarrow não instalado. Salvando como JSON.")
            with open(output_file.replace(".parquet", ".json"), "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False, default=str)


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
        help="Filtrar tipo de transação (type ou operationType)",
    )
    parser.add_argument(
        "--only",
        choices=["connectors", "items", "accounts", "transactions", "investments", "loans", "bills", "all"],
        default="all",
        help="O que buscar",
    )
    parser.add_argument("--output", help="Arquivo de saída")
    parser.add_argument(
        "--format",
        choices=["json", "csv", "parquet"],
        default="json",
        help="Formato do arquivo de saída (padrão: json)",
    )
    parser.add_argument("--quiet", "-q", action="store_true", help="Menos output")
    parser.add_argument(
        "--all-transactions",
        action="store_true",
        help="Busca transações de TODOS os bancos em items.json (usa API v2)",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Desabilita cache de conectores/contas",
    )
    args = parser.parse_args()

    items_map = load_items_map()

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

        if args.output:
            write_output(all_data, args.output, args.format)
            if not args.quiet:
                print(f"\n💾 Dados salvos em: {args.output} ({args.format})")
        if not args.quiet:
            print("\n✅ Concluído!")
        sys.exit(0)

    try:
        use_cache = not args.no_cache

        # 1. CONECTORES / BANCOS
        if args.only in ("connectors", "all"):
            if not args.quiet:
                print("\n🔌 Buscando conectores/bancos...")
            connectors = client.fetch_connectors(use_cache=use_cache)
            all_data["connectors"] = connectors
            if not args.quiet:
                print_summary("Conectores", connectors, ["id", "name", "type", "country"])

        # 2. ITENS - só busca se NÃO temos item_id explícito
        if args.only in ("items", "all") and not args.item_id:
            if not args.quiet:
                print("\n📦 Buscando itens (conexões)...")
            try:
                items = client.fetch_items()
                all_data["items"] = items
                if not args.quiet:
                    print_summary("Itens", items, ["id", "name", "connectorId", "status", "updatedAt"])
            except requests.HTTPError as e:
                if e.response.status_code == 401 and args.item_name:
                    # Esperado para contas pessoais - já temos item_id via items.json
                    if not args.quiet:
                        print("  ℹ️  /items retorna 401 para contas pessoais (usando items.json)")
                else:
                    raise

        target_item_id = args.item_id
        if not target_item_id and all_data.get("items"):
            target_item_id = all_data["items"][0]["id"]
            if not args.quiet:
                print(f"\n📌 Usando primeiro item: {target_item_id}")

        if target_item_id:
            # 3. CONTAS - busca se necessário para outras operações
            needs_accounts = args.only in ("accounts", "transactions", "investments", "loans", "bills", "all")
            if needs_accounts:
                if not args.quiet:
                    print(f"\n🏦 Buscando contas do item {target_item_id}...")
                accounts = client.fetch_accounts(target_item_id, use_cache=use_cache)
                all_data["accounts"] = accounts
                if not args.quiet and args.only in ("accounts", "all"):
                    print_summary(
                        "Contas", accounts, ["id", "name", "type", "subtype", "balance", "currency"]
                    )

            credit_accounts = [
                a for a in all_data.get("accounts", []) if a.get("type") in ("CREDIT_CARD", "CREDIT")
            ]
            _ = [a for a in all_data.get("accounts", []) if a.get("type") in ("CHECKING", "SAVINGS")]

            if not args.quiet and credit_accounts and args.only in ("accounts", "all"):
                print_summary(
                    "Cartões de Crédito",
                    credit_accounts,
                    ["id", "name", "balance", "currency", "creditLimit"],
                )

            # 4. TRANSAÇÕES
            if args.only in ("transactions", "all"):
                if not args.quiet:
                    print(f"\n💳 Buscando transações do item {target_item_id}...")
                accounts = all_data.get("accounts", [])
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

                    for tx in transactions:
                        tx["_account_id"] = account_id
                        tx["_account_name"] = account_name

                    all_transactions.extend(transactions)

                all_data["transactions"] = all_transactions
                if not args.quiet:
                    print_summary(
                        "Transações",
                        all_transactions,
                        ["id", "description", "amount", "currency", "date", "type"],
                    )

                if all_transactions and not args.quiet:
                    total_credit = sum(t.get("amount", 0) for t in all_transactions if t.get("amount", 0) > 0)
                    total_debit = sum(t.get("amount", 0) for t in all_transactions if t.get("amount", 0) < 0)
                    net = total_credit + total_debit
                    print(
                        f"\n💰 Resumo: Créditos: {total_credit:.2f} | "
                        f"Débitos: {total_debit:.2f} | Líquido: {net:.2f}"
                    )

            # 5. INVESTIMENTOS
            if args.only in ("investments", "all"):
                if not args.quiet:
                    print("\n📈 Buscando investimentos...")
                investments = client.fetch_investments(target_item_id)
                all_data["investments"] = investments
                if not args.quiet:
                    print_summary(
                        "Investimentos", investments, ["id", "name", "type", "amount", "currency"]
                    )

            # 6. EMPRÉSTIMOS
            if args.only in ("loans", "all"):
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

            # 7. FATURAS CARTÃO
            if args.only in ("bills", "all"):
                if not args.quiet:
                    print("\n🧾 Buscando faturas cartão...")
                all_bills = []
                for account in all_data.get("accounts", []):
                    if account.get("type") in ("CREDIT_CARD", "CREDIT"):
                        account_id = account.get("id")
                        if not args.quiet:
                            print(f"  💳 Buscando faturas: {account.get('name')} ({account_id})")
                        try:
                            bills = client.fetch_bills(account_id)
                            all_bills.extend(bills)
                        except requests.HTTPError as e:
                            if not args.quiet:
                                print(f"    ⚠️  Erro HTTP {e.response.status_code}")
                        except (requests.RequestException, ValueError, KeyError, OSError) as e:
                            if not args.quiet:
                                print(f"    ⚠️  Erro: {e}")
                all_data["bills"] = all_bills
                if not args.quiet:
                    print_summary(
                        "Faturas",
                        all_bills,
                        ["id", "accountId", "totalAmount", "currency", "dueDate", "status"],
                    )

    except requests.HTTPError as e:
        print(f"❌ Erro HTTP {e.response.status_code}: {e.response.text}", file=sys.stderr)
        sys.exit(1)
    except (requests.RequestException, ValueError, KeyError, OSError) as e:
        print(f"❌ Erro: {e}", file=sys.stderr)
        sys.exit(1)

    if args.output:
        write_output(all_data, args.output, args.format)
        if not args.quiet:
            print(f"\n💾 Dados salvos em: {args.output} ({args.format})")

    if not args.quiet:
        print("\n✅ Concluído!")


if __name__ == "__main__":
    main()
