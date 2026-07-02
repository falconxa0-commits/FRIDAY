import logging
import datetime
from typing import Optional

from integrations.base import BaseIntegration
from integrations.crypto_tracker import CryptoTrackerIntegration

logger = logging.getLogger(__name__)


class FinanceIntegration(BaseIntegration):
    """Personal finance tracker with budget analysis and investment insights.

    Delegates crypto price look-ups to :class:`CryptoTrackerIntegration`.
    All other financial data (budget, spending) is stored locally in memory
    and can be persisted to a JSON file.
    """

    @property
    def name(self) -> str:
        return "Finance"

    def __init__(self):
        self.crypto = CryptoTrackerIntegration()
        self.monthly_budget: float = 500_000  # example in Naira
        self.spending: list[dict] = []  # list of {"amount": float, "category": str, "date": str}

    def available(self) -> bool:
        return True

    def list_actions(self):
        return [
            "get_financial_status",
            "analyze_investment",
            "add_expense",
            "set_budget",
            "spending_breakdown",
        ]

    async def execute(self, action: str, params: Optional[dict] = None) -> dict:
        params = params or {}

        try:
            if action == "get_financial_status":
                return await self._get_financial_status(params)
            elif action == "analyze_investment":
                return await self._analyze_investment(params)
            elif action == "add_expense":
                return self._add_expense(params)
            elif action == "set_budget":
                return self._set_budget(params)
            elif action == "spending_breakdown":
                return self._spending_breakdown(params)
            else:
                return self._make_response(
                    "not_implemented",
                    f"Action '{action}' is not supported by Finance.",
                )
        except Exception as exc:
            logger.error("Finance execute failed: %s", exc)
            return self._make_response("error", str(exc))

    # ---- action implementations -------------------------------------

    async def _get_financial_status(self, params: dict) -> dict:
        btc_result = await self.crypto.execute("get_price", {"coin": "bitcoin"})
        btc_msg = btc_result.get("message", "Bitcoin price unavailable.")

        total_spent = sum(e["amount"] for e in self.spending)
        remaining = self.monthly_budget - total_spent
        pct_used = (total_spent / self.monthly_budget * 100) if self.monthly_budget else 0

        warnings = []
        if pct_used > 80:
            warnings.append(
                "⚠️ You've exceeded 80% of your monthly budget. "
                "Consider reducing non-essential spending."
            )
        if pct_used > 100:
            warnings.append("🔴 Budget exceeded! Immediate review recommended.")

        status = (
            f"Financial Snapshot: ₦{remaining:,.0f} remaining of "
            f"₦{self.monthly_budget:,.0f} budget ({pct_used:.1f}% used). "
            f"{btc_msg}"
        )
        if warnings:
            status += "\n" + "\n".join(warnings)

        return self._make_response(
            "success",
            status,
            receipt_data={
                "monthly_budget": self.monthly_budget,
                "total_spent": total_spent,
                "remaining": remaining,
                "pct_used": pct_used,
                "warnings": warnings,
            },
        )

    async def _analyze_investment(self, params: dict) -> dict:
        asset = params.get("asset", "bitcoin")
        market_result = await self.crypto.execute(
            "get_market_data", {"coin": asset}
        )
        market_data = market_result.get("receipt", {}).get("data", {}) if market_result.get("receipt") else {}

        # Real analysis based on available data
        change_24h = market_data.get("change_24h_pct")
        recommendation = self._compute_recommendation(change_24h)

        return self._make_response(
            "success",
            (
                f"Analysis for {asset}: {market_result.get('message', 'Price data unavailable.')}. "
                f"Recommendation: {recommendation}"
            ),
            receipt_data={
                "asset": asset,
                "market_data": market_data,
                "recommendation": recommendation,
            },
        )

    @staticmethod
    def _compute_recommendation(change_24h: Optional[float]) -> str:
        """Simple rule-based recommendation from 24h price change."""
        if change_24h is None:
            return "Insufficient data for analysis. Monitor closely before making decisions."
        if change_24h > 5:
            return "Strong upward momentum — consider taking partial profits."
        if change_24h > 1:
            return "Mild uptrend — holding is reasonable."
        if change_24h > -1:
            return "Sideways movement — hold and watch for breakout signals."
        if change_24h > -5:
            return "Moderate decline — dollar-cost averaging may be prudent."
        return "Sharp decline — assess risk tolerance before averaging down."

    def _add_expense(self, params: dict) -> dict:
        amount = params.get("amount")
        category = params.get("category", "uncategorized")
        if amount is None:
            return self._make_response("error", "Missing 'amount' parameter.")
        self.spending.append(
            {
                "amount": float(amount),
                "category": category,
                "date": datetime.datetime.now().isoformat(),
            }
        )
        total = sum(e["amount"] for e in self.spending)
        return self._make_response(
            "success",
            f"Added ₦{amount:,.0f} ({category}). Total spending: ₦{total:,.0f}.",
            receipt_data={"total_spent": total},
        )

    def _set_budget(self, params: dict) -> dict:
        budget = params.get("budget")
        if budget is None:
            return self._make_response("error", "Missing 'budget' parameter.")
        self.monthly_budget = float(budget)
        return self._make_response(
            "success",
            f"Monthly budget set to ₦{self.monthly_budget:,.0f}.",
            receipt_data={"monthly_budget": self.monthly_budget},
        )

    def _spending_breakdown(self, params: dict) -> dict:
        breakdown: dict[str, float] = {}
        for entry in self.spending:
            cat = entry["category"]
            breakdown[cat] = breakdown.get(cat, 0) + entry["amount"]
        total = sum(breakdown.values())
        lines = [f"  {cat}: ₦{amt:,.0f} ({amt / total * 100:.1f}%)" for cat, amt in breakdown.items()] if total else ["  No spending recorded."]
        msg = f"Spending breakdown (total ₦{total:,.0f}):\n" + "\n".join(lines)
        return self._make_response("success", msg, receipt_data={"breakdown": breakdown, "total": total})
