"""
Sector Mapping Auto-Update Service

Features:
  - Weekly scan of Binance Top 200 futures
  - Identify unclassified new coins
  - AI-assisted classification proposals
  - Push admin review notifications
  - One-click approval with hot-reload

Flow:
  1. Fetch Top 200 futures (by volume)
  2. Compare against existing mapping, find new coins
  3. Classify new coins via AI (OpenRouter/Grok)
  4. Push Telegram notification to admin
  5. Admin clicks [Approve] to confirm
  6. Update sector_mapping.json, trigger hot reload
"""

import json
import hashlib
import time
import requests
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Set, Any, Tuple

from src.core.structured_logger import get_logger

from src.core.config import settings


class SectorUpdater:
    """Sector mapping auto-update service"""

    # Binance API
    BINANCE_API = "https://fapi.binance.com"

    # Config path
    CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "sector_mapping.json"

    # Pending proposal storage path (local backup)
    PENDING_PATH = (
        Path(__file__).parent.parent.parent / "config" / "sector_pending.json"
    )

    # Redis key for pending proposal
    REDIS_PENDING_KEY = "ignis:sector:pending_proposal"

    # Sector definitions for AI classification reference
    SECTOR_DEFINITIONS = {
        "AI": "Artificial intelligence, machine learning, AI agents, data computing projects (e.g. RNDR, FET, TAO)",
        "Meme": "Community-driven meme tokens (e.g. DOGE, SHIB, PEPE, WIF)",
        "RWA": "Real-world asset tokenization, DeFi infrastructure (e.g. ONDO, PENDLE, MKR)",
        "L1": "Layer 1 blockchains, smart contract platforms (e.g. SOL, AVAX, NEAR, SUI)",
        "L2": "Layer 2 scaling solutions, rollups (e.g. OP, ARB, STRK, ZK)",
        "DeFi": "Decentralized exchanges, lending, derivatives protocols (e.g. UNI, CRV, DYDX, GMX)",
        "BTC Eco": "Bitcoin ecosystem, Ordinals, Runes (e.g. STX, ORDI, RUNE)",
        "GameFi": "Blockchain gaming, metaverse, NFT games (e.g. IMX, GALA, AXS, NOT)",
        "DePIN": "Decentralized physical infrastructure, storage, compute (e.g. FIL, AR, HNT, RENDER)",
        "Oracle": "Oracles, off-chain data services (e.g. LINK, BAND, API3, PYTH)",
        "Privacy": "Privacy coins, zero-knowledge proof projects (e.g. ZEC, XMR, SCRT, AZTEC)",
        "Social": "Social protocols, SocialFi, content platforms (e.g. LENS, FRIEND, DESO)",
    }

    # CoinGecko API config
    COINGECKO_API_BASE = "https://api.coingecko.com/api/v3"
    COINGECKO_BATCH_SIZE = 10  # coins per batch
    COINGECKO_BATCH_DELAY = 25  # seconds between batches to avoid rate limiting

    # CoinGecko Category → Ignis Sector mapping
    COINGECKO_CATEGORY_MAP = {
        # L1
        "layer-1": "L1",
        "smart-contract-platform": "L1",
        "proof-of-stake-pos": "L1",
        "proof-of-work-pow": "L1",
        # L2
        "layer-2": "L2",
        "optimistic-rollups": "L2",
        "zero-knowledge-zk": "L2",
        "arbitrum-ecosystem": "L2",
        "optimism-ecosystem": "L2",
        "polygon-ecosystem": "L2",
        # DeFi
        "decentralized-finance-defi": "DeFi",
        "decentralized-exchange-dex": "DeFi",
        "lending-borrowing": "DeFi",
        "yield-farming": "DeFi",
        "liquid-staking": "DeFi",
        "derivatives": "DeFi",
        "yield-aggregator": "DeFi",
        "automated-market-maker-amm": "DeFi",
        # Meme
        "meme-token": "Meme",
        "meme": "Meme",  # CoinGecko sometimes returns just "Meme"
        "dog-themed-coins": "Meme",
        "dog-themed": "Meme",  # handle variant format
        "cat-themed-coins": "Meme",
        "frog-themed-coins": "Meme",
        "solana-meme-coins": "Meme",
        # AI
        "artificial-intelligence": "AI",
        "ai-agents": "AI",
        "machine-learning": "AI",
        "big-data": "AI",
        # GameFi
        "gaming": "GameFi",
        "play-to-earn": "GameFi",
        "metaverse": "GameFi",
        "nft-gaming": "GameFi",
        "move-to-earn": "GameFi",
        # DePIN
        "decentralized-storage": "DePIN",
        "file-sharing": "DePIN",
        "distributed-computing": "DePIN",
        "iot": "DePIN",
        "wireless-network": "DePIN",
        # RWA
        "real-world-assets": "RWA",
        "tokenized-securities": "RWA",
        "asset-backed-tokens": "RWA",
        # BTC Eco
        "bitcoin-ecosystem": "BTC Eco",
        "ordinals": "BTC Eco",
        "brc-20": "BTC Eco",
        "runes": "BTC Eco",
        "stacks-ecosystem": "BTC Eco",
        # Oracle
        "oracle": "Oracle",
        "data-availability": "Oracle",
        # Privacy
        "privacy-coins": "Privacy",
        "zero-knowledge-proofs": "Privacy",
        # Social
        "socialfi": "Social",
        "social-money": "Social",
        "fan-token": "Social",
    }

    # Top N coins
    TOP_N = 200

    # Minimum volume filter (USD)
    MIN_VOLUME = 5_000_000

    def __init__(self):
        self.logger = get_logger(__name__)
        self._current_mapping: Dict[str, List[str]] = {}
        self._current_version: str = ""
        self._load_current_mapping()

    def _load_current_mapping(self) -> None:
        """Load current sector mapping"""
        try:
            if self.CONFIG_PATH.exists():
                with open(self.CONFIG_PATH, "r", encoding="utf-8") as f:
                    config = json.load(f)
                self._current_mapping = config.get("sectors", {})
                self._current_version = config.get("version", "1.0")
                self.logger.info(
                    f"Sector mapping loaded: {len(self._current_mapping)} sectors, "
                    f"version {self._current_version}"
                )
        except Exception as e:
            self.logger.error(f"Failed to load sector mapping: {e}")

    def get_all_mapped_symbols(self) -> Set[str]:
        """Get all classified symbols"""
        symbols = set()
        for coins in self._current_mapping.values():
            for coin in coins:
                symbols.add(coin.upper())
        return symbols

    def fetch_top_coins(self) -> List[Dict[str, Any]]:
        """
        Fetch Top N futures (sorted by 24h volume)

        Returns:
            [{"symbol": "BTCUSDT", "volume_usd": 1000000000, "price": 95000}, ...]
        """
        try:
            resp = requests.get(f"{self.BINANCE_API}/fapi/v1/ticker/24hr", timeout=15)
            resp.raise_for_status()
            tickers = resp.json()

            # Filter USDT perpetual futures
            usdt_pairs = [
                t
                for t in tickers
                if t["symbol"].endswith("USDT") and not t["symbol"].endswith("_PERP")
            ]

            # Sort by volume
            sorted_pairs = sorted(
                usdt_pairs, key=lambda x: float(x.get("quoteVolume", 0)), reverse=True
            )

            # Take Top N and filter by minimum volume
            result = []
            for t in sorted_pairs[: self.TOP_N]:
                volume = float(t.get("quoteVolume", 0))
                if volume >= self.MIN_VOLUME:
                    result.append(
                        {
                            "symbol": t["symbol"],
                            "base_symbol": t["symbol"].replace("USDT", ""),
                            "volume_usd": volume,
                            "price": float(t.get("lastPrice", 0)),
                            "change_24h": float(t.get("priceChangePercent", 0)),
                        }
                    )

            self.logger.info(
                f"Top futures fetched: {len(result)} (filtered <${self.MIN_VOLUME / 1e6:.0f}M)"
            )
            return result

        except Exception as e:
            self.logger.error(f"Failed to fetch top futures: {e}")
            return []

    def find_new_coins(self, top_coins: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Identify unclassified new coins

        Args:
            top_coins: Top N futures list

        Returns:
            New coins not in any sector
        """
        mapped = self.get_all_mapped_symbols()

        # Exclude major coins that don't need classification
        EXCLUDE = {"BTC", "ETH", "BNB", "XRP", "USDC", "USDT", "TUSD", "BUSD", "DAI"}

        new_coins = []
        for coin in top_coins:
            base = coin["base_symbol"].upper()
            if base not in mapped and base not in EXCLUDE:
                new_coins.append(coin)

        if new_coins:
            self.logger.info(
                f"New coins detected: {len(new_coins)} - {[c['base_symbol'] for c in new_coins[:5]]}..."
            )

        return new_coins

    # ========== CoinGecko API Methods ==========

    def _setup_session(self) -> requests.Session:
        """Configure HTTP session"""
        session = requests.Session()
        session.headers.update(
            {"Accept": "application/json", "User-Agent": "Ignis-SectorUpdater/1.0"}
        )
        return session

    def _search_coin_id(
        self, symbol: str, session: Optional[requests.Session] = None
    ) -> Optional[str]:
        """
        Search CoinGecko coin_id by symbol

        Args:
            symbol: Coin symbol (e.g. LINK, HYPE)
            session: HTTP session

        Returns:
            coin_id or None
        """
        if session is None:
            session = self._setup_session()

        try:
            resp = session.get(
                f"{self.COINGECKO_API_BASE}/search",
                params={"query": symbol},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()

            coins = data.get("coins", [])
            if not coins:
                return None

            # Sort by market_cap_rank, pick highest-ranked match
            # Resolves symbol conflicts like HYPE
            best_match = None
            best_rank = float("inf")

            for coin in coins:
                # Exact symbol match (case-insensitive)
                if coin.get("symbol", "").upper() == symbol.upper():
                    rank = coin.get("market_cap_rank")
                    if rank is not None and rank < best_rank:
                        best_rank = rank
                        best_match = coin.get("id")

            return best_match

        except Exception as e:
            self.logger.debug(f"coin_id search failed for {symbol}: {e}")
            return None

    def _get_coin_categories(
        self, coin_id: str, session: Optional[requests.Session] = None
    ) -> List[str]:
        """
        Get categories list for a coin

        Args:
            coin_id: CoinGecko coin ID
            session: HTTP session

        Returns:
            categories list
        """
        if session is None:
            session = self._setup_session()

        try:
            resp = session.get(
                f"{self.COINGECKO_API_BASE}/coins/{coin_id}",
                params={
                    "localization": "false",
                    "tickers": "false",
                    "market_data": "false",
                    "community_data": "false",
                    "developer_data": "false",
                },
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()

            return data.get("categories", [])

        except Exception as e:
            self.logger.debug(f"Failed to get categories for {coin_id}: {e}")
            return []

    # High-priority categories (direct functional descriptors, take precedence over ecosystem tags)
    HIGH_PRIORITY_CATEGORIES = {
        # Oracle
        "oracle",
        # Privacy
        "privacy-coins",
        # Meme (high priority, avoid being overridden by ecosystem tags)
        "meme-token",
        "meme",
        "dog-themed",
        "dog-themed-coins",
        # AI
        "artificial-intelligence",
        "ai-agents",
        # DeFi
        "decentralized-finance-defi",
        "decentralized-exchange-dex",
        "lending-borrowing",
        # GameFi
        "gaming",
        "play-to-earn",
        "metaverse",
        # DePIN
        "decentralized-storage",
        # RWA
        "real-world-assets",
        # Social
        "socialfi",
        # L1/L2 (specific tags only, excludes generic smart-contract-platform)
        "layer-1",
        "layer-2",
    }

    def _classify_single_coingecko(
        self, symbol: str, session: Optional[requests.Session] = None
    ) -> Optional[str]:
        """
        Classify a single coin using CoinGecko

        Args:
            symbol: Coin symbol
            session: HTTP session

        Returns:
            Sector name or None
        """
        coin_id = self._search_coin_id(symbol, session)
        if not coin_id:
            return None

        categories = self._get_coin_categories(coin_id, session)
        if not categories:
            return None

        # Map categories to sector, prioritizing high-priority categories
        high_priority_match = None
        low_priority_match = None

        for cat in categories:
            cat_lower = cat.lower().replace(" ", "-")
            if cat_lower in self.COINGECKO_CATEGORY_MAP:
                sector = self.COINGECKO_CATEGORY_MAP[cat_lower]
                # High-priority: return immediately
                if cat_lower in self.HIGH_PRIORITY_CATEGORIES:
                    return sector
                # Low-priority (e.g. ecosystem tags): record but keep searching
                if low_priority_match is None:
                    low_priority_match = sector

        return low_priority_match

    def _classify_with_coingecko(
        self, coins: List[Dict[str, Any]]
    ) -> Tuple[Dict[str, str], List[Dict[str, Any]]]:
        """
        Batch classify using CoinGecko

        Args:
            coins: Coins to classify

        Returns:
            (classified: {symbol: sector}, uncovered: coins not found in CoinGecko)
        """
        classified: Dict[str, str] = {}
        uncovered: List[Dict[str, Any]] = []
        session = self._setup_session()

        total = len(coins)
        for i, coin in enumerate(coins):
            symbol = coin["base_symbol"].upper()

            sector = self._classify_single_coingecko(symbol, session)
            if sector:
                classified[symbol] = sector
                self.logger.debug(f"CoinGecko classified: {symbol} -> {sector}")
            else:
                uncovered.append(coin)

            # Pause between batches to avoid rate limiting
            if (i + 1) % self.COINGECKO_BATCH_SIZE == 0 and (i + 1) < total:
                self.logger.info(
                    f"CoinGecko progress: {i + 1}/{total}, "
                    f"pausing {self.COINGECKO_BATCH_DELAY}s to avoid rate limit..."
                )
                time.sleep(self.COINGECKO_BATCH_DELAY)

        self.logger.info(
            f"CoinGecko classification done: {len(classified)} classified, {len(uncovered)} uncovered"
        )
        return classified, uncovered

    # ========== Grok Online Fallback ==========

    def _classify_with_grok_online(
        self, coins: List[Dict[str, Any]], timeout: int = 60
    ) -> Dict[str, str]:
        """
        Classify uncovered coins using Grok Online (Fallback)

        Args:
            coins: Coins to classify
            timeout: API timeout

        Returns:
            {symbol: sector} classification result
        """
        api_key = settings.get_secret("OPENROUTER_API_KEY")
        if not api_key:
            self.logger.warning("OPENROUTER_API_KEY not configured, skipping Grok classification")
            return {}

        if not coins:
            return {}

        # Build prompt - no 20-coin limit
        coin_list = ", ".join([c["base_symbol"] for c in coins])

        sector_desc = "\n".join(
            [f"- {name}: {desc}" for name, desc in self.SECTOR_DEFINITIONS.items()]
        )

        prompt = f"""Classify the following cryptocurrencies into their sectors. Search online for information about these coins before classifying.

Available sectors:
{sector_desc}

Coins to classify: {coin_list}

Reply in JSON format: {{"SYMBOL": "SectorName", ...}}
If a coin's sector cannot be determined, mark it as "Other".
Return JSON only, no other text."""

        try:
            payload = {
                "model": "x-ai/grok-4.1-fast:online",  # online model
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 2000,
                "temperature": 0.3,
            }

            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/73nuts/crypto-signal-bot",
                "X-Title": "Ignis Sector Classifier",
            }

            self.logger.info(f"Grok Online classification request: {len(coins)} coins")

            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=timeout,
            )
            response.raise_for_status()

            result = response.json()
            content = result["choices"][0]["message"]["content"]

            # Extract JSON
            import re

            json_match = re.search(r"\{[^{}]*\}", content, re.DOTALL)
            if json_match:
                classification = json.loads(json_match.group())
                self.logger.info(f"Grok Online classification done: {len(classification)}")
                return classification
            else:
                self.logger.warning(f"Grok returned unparseable response: {content[:200]}")
                return {}

        except Exception as e:
            self.logger.error(f"Grok Online classification failed: {e}")
            return {}

    # ========== Hybrid Classification ==========

    def classify_with_hybrid(self, coins: List[Dict[str, Any]]) -> Dict[str, str]:
        """
        Hybrid classification: CoinGecko (primary) + Grok Online (fallback)

        Args:
            coins: Coins to classify

        Returns:
            {symbol: sector} classification result
        """
        if not coins:
            return {}

        self.logger.info(f"Starting hybrid classification: {len(coins)} coins")

        # Step 1: CoinGecko classification
        cg_classified, uncovered = self._classify_with_coingecko(coins)

        # Step 2: Grok Online for uncovered coins
        grok_classified = {}
        if uncovered:
            self.logger.info(f"Using Grok Online to classify {len(uncovered)} uncovered coins")
            grok_classified = self._classify_with_grok_online(uncovered)

        # Merge results
        result = {**cg_classified, **grok_classified}

        self.logger.info(
            f"Hybrid classification done: {len(result)} total "
            f"(CoinGecko:{len(cg_classified)}, Grok:{len(grok_classified)})"
        )

        return result

    def classify_with_ai(
        self, coins: List[Dict[str, Any]], timeout: int = 30
    ) -> Dict[str, str]:
        """
        Classify new coins into sectors using AI

        Args:
            coins: New coins list
            timeout: API timeout

        Returns:
            {symbol: sector} classification result
        """
        api_key = settings.get_secret("OPENROUTER_API_KEY")
        if not api_key:
            self.logger.warning("OPENROUTER_API_KEY not configured, skipping AI classification")
            return {}

        if not coins:
            return {}

        # Build prompt
        coin_list = ", ".join([c["base_symbol"] for c in coins[:20]])  # limit to 20

        sector_desc = "\n".join(
            [f"- {name}: {desc}" for name, desc in self.SECTOR_DEFINITIONS.items()]
        )

        prompt = f"""Classify the following cryptocurrencies into their sectors.

Available sectors:
{sector_desc}

Coins to classify: {coin_list}

Reply in JSON format: {{"SYMBOL": "SectorName", ...}}
If a coin's sector cannot be determined, mark it as "Other".
Return JSON only, no other text."""

        try:
            payload = {
                "model": "x-ai/grok-4.1-fast",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 500,
                "temperature": 0.3,
            }

            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/73nuts/crypto-signal-bot",
                "X-Title": "Ignis Sector Classifier",
            }

            self.logger.info(f"AI classification request: {len(coins)} coins")

            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=timeout,
            )
            response.raise_for_status()

            result = response.json()
            content = result["choices"][0]["message"]["content"]

            # Extract JSON
            import re

            json_match = re.search(r"\{[^{}]*\}", content, re.DOTALL)
            if json_match:
                classification = json.loads(json_match.group())
                self.logger.info(f"AI classification done: {classification}")
                return classification
            else:
                self.logger.warning(f"AI returned unparseable response: {content[:200]}")
                return {}

        except Exception as e:
            self.logger.error(f"AI classification failed: {e}")
            return {}

    def create_update_proposal(
        self, new_coins: List[Dict[str, Any]], classification: Dict[str, str]
    ) -> Dict[str, Any]:
        """
        Create an update proposal

        Args:
            new_coins: New coins list
            classification: AI classification result

        Returns:
            Update proposal data structure
        """
        # Aggregate by sector
        by_sector: Dict[str, List[Dict[str, Any]]] = {}
        unclassified: List[Dict[str, Any]] = []

        for coin in new_coins:
            symbol = coin["base_symbol"].upper()
            sector = classification.get(
                symbol, classification.get(coin["base_symbol"], None)
            )

            if sector and sector != "Other" and sector in self.SECTOR_DEFINITIONS:
                if sector not in by_sector:
                    by_sector[sector] = []
                by_sector[sector].append(
                    {
                        "symbol": symbol,
                        "volume_usd": coin["volume_usd"],
                        "change_24h": coin["change_24h"],
                    }
                )
            else:
                unclassified.append(
                    {
                        "symbol": symbol,
                        "volume_usd": coin["volume_usd"],
                        "ai_suggestion": sector or "Unknown",
                    }
                )

        # Generate proposal ID (for callback identification)
        proposal_id = hashlib.md5(
            f"{datetime.now().isoformat()}{len(new_coins)}".encode()
        ).hexdigest()[:8]

        proposal = {
            "id": proposal_id,
            "created_at": datetime.now().isoformat(),
            "by_sector": by_sector,
            "unclassified": unclassified,
            "total_new": len(new_coins),
            "total_classified": sum(len(v) for v in by_sector.values()),
        }

        # Save to pending
        self._save_pending(proposal)

        return proposal

    def _save_pending(self, proposal: Dict[str, Any]) -> None:
        """Save pending proposal to Redis (shared across containers)"""
        proposal_json = json.dumps(proposal, ensure_ascii=False)

        # 1. Save via redis-py directly (avoid async context issues)
        try:
            import redis

            redis_host = settings.REDIS_HOST
            redis_port = settings.REDIS_PORT
            redis_password = settings.REDIS_PASSWORD.get_secret_value() if settings.REDIS_PASSWORD else None

            r = redis.Redis(
                host=redis_host,
                port=redis_port,
                password=redis_password,
                decode_responses=True,
            )
            r.setex(self.REDIS_PENDING_KEY, 86400, proposal_json)
            self.logger.info(f"Pending proposal saved to Redis: {proposal['id']}")
        except Exception as e:
            self.logger.warning(f"Redis save failed: {e}")

        # 2. Local file backup
        try:
            with open(self.PENDING_PATH, "w", encoding="utf-8") as f:
                json.dump(proposal, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.logger.error(f"Local backup save failed: {e}")

    def load_pending(self) -> Optional[Dict[str, Any]]:
        """Load pending proposal from Redis"""
        # 1. Try Redis first
        try:
            import redis

            redis_host = settings.REDIS_HOST
            redis_port = settings.REDIS_PORT
            redis_password = settings.REDIS_PASSWORD.get_secret_value() if settings.REDIS_PASSWORD else None

            r = redis.Redis(
                host=redis_host,
                port=redis_port,
                password=redis_password,
                decode_responses=True,
            )
            proposal_json = r.get(self.REDIS_PENDING_KEY)
            if proposal_json:
                return json.loads(proposal_json)
        except Exception as e:
            self.logger.warning(f"Redis load failed: {e}")

        # 2. Fallback to local file
        try:
            if self.PENDING_PATH.exists():
                with open(self.PENDING_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            self.logger.error(f"Local backup load failed: {e}")

        return None

    def _clear_pending(self) -> None:
        """Clear pending proposal"""
        # 1. Clear Redis
        try:
            import redis

            redis_host = settings.REDIS_HOST
            redis_port = settings.REDIS_PORT
            redis_password = settings.REDIS_PASSWORD.get_secret_value() if settings.REDIS_PASSWORD else None

            r = redis.Redis(
                host=redis_host,
                port=redis_port,
                password=redis_password,
                decode_responses=True,
            )
            r.delete(self.REDIS_PENDING_KEY)
        except Exception as e:
            self.logger.warning(f"Redis clear failed: {e}")

        # 2. Clear local file
        try:
            if self.PENDING_PATH.exists():
                self.PENDING_PATH.unlink()
        except Exception as e:
            self.logger.error(f"Failed to clear pending proposal: {e}")

    def apply_proposal(self, proposal_id: str) -> Tuple[bool, str]:
        """
        Apply an update proposal

        Args:
            proposal_id: Proposal ID

        Returns:
            (success, message)
        """
        pending = self.load_pending()
        if not pending:
            return False, "No pending proposal"

        if pending.get("id") != proposal_id:
            return False, f"Proposal ID mismatch: {proposal_id}"

        try:
            # Load current config
            with open(self.CONFIG_PATH, "r", encoding="utf-8") as f:
                config = json.load(f)

            # Merge new coins into sectors
            by_sector = pending.get("by_sector", {})
            added_count = 0

            for sector, coins in by_sector.items():
                if sector not in config["sectors"]:
                    config["sectors"][sector] = []

                for coin in coins:
                    symbol = coin["symbol"]
                    if symbol not in config["sectors"][sector]:
                        config["sectors"][sector].append(symbol)
                        added_count += 1

            # Update version and date
            old_version = config.get("version", "1.0")
            version_parts = old_version.split(".")
            new_minor = int(version_parts[1]) + 1 if len(version_parts) > 1 else 1
            config["version"] = f"{version_parts[0]}.{new_minor}"
            config["updated"] = datetime.now().strftime("%Y-%m-%d")

            # Atomic write (write to temp file then rename)
            temp_path = self.CONFIG_PATH.with_suffix(".tmp")
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            temp_path.replace(self.CONFIG_PATH)

            # Clear pending
            self._clear_pending()

            # Trigger hot reload
            self._trigger_hot_reload()

            msg = f"Added {added_count} coins, version updated to {config['version']}"
            self.logger.info(msg)
            return True, msg

        except Exception as e:
            self.logger.error(f"Failed to apply proposal: {e}")
            return False, str(e)

    def reject_proposal(self, proposal_id: str) -> Tuple[bool, str]:
        """
        Reject an update proposal

        Args:
            proposal_id: Proposal ID

        Returns:
            (success, message)
        """
        pending = self.load_pending()
        if not pending:
            return False, "No pending proposal"

        if pending.get("id") != proposal_id:
            return False, f"Proposal ID mismatch: {proposal_id}"

        try:
            self._clear_pending()
            return True, "Update proposal rejected"
        except Exception as e:
            return False, str(e)

    def _trigger_hot_reload(self) -> None:
        """Trigger SectorAggregator hot reload"""
        try:
            from src.scanner.sector_aggregator import get_sector_aggregator

            aggregator = get_sector_aggregator()
            if aggregator.reload():
                self.logger.info(
                    f"SectorAggregator hot reload complete, version {aggregator.version}"
                )
            else:
                self.logger.warning("SectorAggregator hot reload failed")
        except Exception as e:
            self.logger.warning(f"Hot reload failed (restart required): {e}")

    def check_and_notify(self) -> Optional[Dict[str, Any]]:
        """
        Check for new coins and generate an update proposal

        Returns:
            Update proposal if new coins found, otherwise None
        """
        # 1. Fetch top futures
        top_coins = self.fetch_top_coins()
        if not top_coins:
            self.logger.warning("Failed to fetch top futures")
            return None

        # 2. Identify new coins
        new_coins = self.find_new_coins(top_coins)
        if not new_coins:
            self.logger.info("No new coins to classify")
            return None

        # 3. Hybrid classification (CoinGecko + Grok Online)
        classification = self.classify_with_hybrid(new_coins)

        # 4. Create proposal
        proposal = self.create_update_proposal(new_coins, classification)

        return proposal

    def format_proposal_message(self, proposal: Dict[str, Any]) -> str:
        """
        Format proposal message for Telegram notification

        Args:
            proposal: Update proposal

        Returns:
            MarkdownV2-formatted message
        """
        lines = [
            "*🔄 Sector Mapping Update Proposal*",
            "",
            f"Detected *{proposal['total_new']}* new coins",
            f"Classified: *{proposal['total_classified']}*",
            "",
        ]

        # Show by sector
        by_sector = proposal.get("by_sector", {})
        if by_sector:
            lines.append("*📊 Classification Results:*")
            for sector, coins in by_sector.items():
                coin_list = ", ".join([c["symbol"] for c in coins[:5]])
                if len(coins) > 5:
                    coin_list += f"... (+{len(coins) - 5})"
                lines.append(f"• {sector}: {coin_list}")
            lines.append("")

        # Unclassified
        unclassified = proposal.get("unclassified", [])
        if unclassified:
            lines.append("*⚠️ Unclassified:*")
            for coin in unclassified[:5]:
                lines.append(f"• {coin['symbol']} (AI suggestion: {coin['ai_suggestion']})")
            if len(unclassified) > 5:
                lines.append(f"  ... (+{len(unclassified) - 5})")
            lines.append("")

        lines.append(f"Proposal ID: `{proposal['id']}`")

        # Escape MarkdownV2 special characters
        text = "\n".join(lines)
        for char in [
            "_",
            "[",
            "]",
            "(",
            ")",
            "~",
            ">",
            "#",
            "+",
            "-",
            "=",
            "|",
            "{",
            "}",
            ".",
            "!",
        ]:
            text = text.replace(char, f"\\{char}")

        return text


# Singleton
_updater: Optional[SectorUpdater] = None


def get_sector_updater() -> SectorUpdater:
    """Get SectorUpdater singleton"""
    global _updater
    if _updater is None:
        _updater = SectorUpdater()
    return _updater
