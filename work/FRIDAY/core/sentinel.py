import logging
import re
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger("EthicalSentinel")


class RiskLevel(str, Enum):
    SAFE = "safe"
    CAUTIOUS = "cautious"
    DANGEROUS = "dangerous"
    CRITICAL = "critical"


@dataclass
class RiskAssessment:
    """Multi-category risk assessment result."""
    data_loss: float = 0.0        # 0-10
    privacy: float = 0.0          # 0-10
    security: float = 0.0         # 0-10
    cost: float = 0.0             # 0-10
    irreversibility: float = 0.0  # 0-10
    overall_score: float = 0.0    # 0-10 weighted
    classification: RiskLevel = RiskLevel.SAFE
    reasons: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)


class EthicalSentinel:
    """Evaluates actions across multiple risk categories with scoring."""

    def __init__(self, ledger=None):
        """
        Args:
            ledger: Optional ActionLedger for enforcement integration.
        """
        self.ledger = ledger
        self._assessment_history: List[RiskAssessment] = []
        self._blocked_actions: List[Dict[str, Any]] = []

        # Weight for each category in overall score
        self._weights = {
            "data_loss": 0.25,
            "privacy": 0.25,
            "security": 0.25,
            "cost": 0.10,
            "irreversibility": 0.15,
        }

        # Keyword-based risk scoring rules
        self._risk_rules = {
            "data_loss": {
                "high": [
                    r"\bdelete\b", r"\bremove\b", r"\bdrop\b",
                    r"\bwipe\b", r"\berase\b", r"\btruncate\b",
                    r"\bdestroy\b", r"\bpurge\b",
                ],
                "medium": [
                    r"\boverwrite\b", r"\breplace\b", r"\bclear\b",
                    r"\breset\b",
                ],
                "low": [
                    r"\bmodify\b", r"\bupdate\b", r"\bchange\b",
                ],
            },
            "privacy": {
                "high": [
                    r"\bshare\b", r"\bsend\b", r"\bpublish\b",
                    r"\bexport\b", r"\bpost\b", r"\bbroadcast\b",
                    r"\bforward\b",
                ],
                "medium": [
                    r"\bread\b", r"\baccess\b", r"\bview\b",
                    r"\bfetch\b", r"\bdownload\b",
                ],
                "low": [
                    r"\bsearch\b", r"\bquery\b", r"\blist\b",
                ],
            },
            "security": {
                "high": [
                    r"\bexecute\b", r"\brun\b", r"\beval\b",
                    r"\bsudo\b", r"\badmin\b", r"\broot\b",
                    r"\bssh\b", r"\bremote\b", r"\binject\b",
                ],
                "medium": [
                    r"\binstall\b", r"\bconfig\b", r"\bpermission\b",
                    r"\bauth\b", r"\blogin\b",
                ],
                "low": [
                    r"\bcheck\b", r"\bstatus\b", r"\bverify\b",
                ],
            },
            "cost": {
                "high": [
                    r"\bbuy\b", r"\bpurchase\b", r"\bsubscribe\b",
                    r"\bprovision\b", r"\bdeploy\b.*\bcluster\b",
                ],
                "medium": [
                    r"\bdeploy\b", r"\bscale\b", r"\bupgrade\b",
                ],
                "low": [
                    r"\btest\b", r"\bpreview\b",
                ],
            },
            "irreversibility": {
                "high": [
                    r"\bpermanent\b", r"\bforever\b", r"\birreversible\b",
                    r"\bfinal\b", r"\bcommit\b",
                ],
                "medium": [
                    r"\bsave\b", r"\bapply\b", r"\bconfirm\b",
                ],
                "low": [
                    r"\bdraft\b", r"\bstaging\b", r"\bpreview\b",
                ],
            },
        }

    # ------------------------------------------------------------------
    # Core evaluation
    # ------------------------------------------------------------------

    # Actions that are NEVER auto-approved regardless of settings.
    # Hardcoded in the sentinel itself — not a convention.
    NEVER_AUTO_APPROVE_PATTERNS = [
        r"\btransfer\b.*\bmoney\b", r"\bbuy\b", r"\bpurchase\b",
        r"\bcheckout\b", r"\bpay\b", r"\bcharge\b",
        r"\bprint\b.*\bprinter\b", r"\b3d.?print\b",
        r"\bdelete\b.*\bfile\b", r"\bwipe\b.*\bdata\b",
        r"\bsend\b.*\bfinancial\b",
    ]

    # Components that are NEVER auto-approved, synced with the ledger.
    # These involve physical-world effects, financial cost, or irreversible
    # actions.  The sentinel checks both action *patterns* and the *component*
    # name to ensure defence-in-depth.
    NEVER_AUTO_APPROVE_COMPONENTS = frozenset({
        "Printer",        # Physical world effect — paper, ink
        "Printer3D",      # Physical world effect — material, heat
        "Finance",        # Financial cost / transactions
        "Commerce",       # Purchase / checkout actions
        "ImageGen",       # Costs money per generation
        "VideoGen",       # Costs money per generation
        "CodeExecution",  # Arbitrary code execution risk
    })

    def evaluate_action(self, action: str, context: Optional[Dict] = None) -> Dict[str, Any]:
        """Evaluate an action across all risk categories.

        Returns a dict with:
            - aligned: bool (True if action is safe to proceed)
            - assessment: RiskAssessment data
            - action required by the caller
        """
        assessment = self._score_action(action, context or {})

        # Hard override: financial/physical/destructive actions are NEVER safe
        action_lower = action.lower()
        never_auto = False
        for pattern in self.NEVER_AUTO_APPROVE_PATTERNS:
            if re.search(pattern, action_lower):
                never_auto = True
                break

        # Hard override: specific components are NEVER auto-approved
        component = (context or {}).get("component", "")
        if component in self.NEVER_AUTO_APPROVE_COMPONENTS:
            never_auto = True

        # Determine classification using max category score (not weighted avg)
        # because a single high-risk category should dominate classification.
        max_cat_score = max(
            assessment.data_loss, assessment.privacy,
            assessment.security, assessment.cost,
            assessment.irreversibility
        )

        if max_cat_score >= 8.0 or never_auto:
            assessment.classification = RiskLevel.DANGEROUS
        elif max_cat_score >= 5.0:
            assessment.classification = RiskLevel.CAUTIOUS
        elif max_cat_score >= 2.5:
            assessment.classification = RiskLevel.CAUTIOUS
        else:
            assessment.classification = RiskLevel.SAFE

        # If multiple categories are high, upgrade to CRITICAL
        high_count = sum(1 for s in [assessment.data_loss, assessment.privacy,
                                      assessment.security, assessment.cost,
                                      assessment.irreversibility] if s >= 8.0)
        if high_count >= 2:
            assessment.classification = RiskLevel.CRITICAL

        # Generate recommendations
        assessment.recommendations = self._generate_recommendations(assessment)

        # Build reasons list
        assessment.reasons = self._build_reasons(action, assessment)

        self._assessment_history.append(assessment)

        # Block critical actions
        aligned = assessment.classification in (RiskLevel.SAFE, RiskLevel.CAUTIOUS)
        if assessment.classification == RiskLevel.CRITICAL:
            self._blocked_actions.append({
                "action": action,
                "score": assessment.overall_score,
                "classification": assessment.classification.value,
            })
            logger.warning(
                f"Blocked critical action: '{action}' "
                f"(score: {assessment.overall_score:.1f})"
            )

        return {
            "aligned": aligned,
            "classification": assessment.classification.value,
            "overall_score": round(assessment.overall_score, 2),
            "categories": {
                "data_loss": round(assessment.data_loss, 2),
                "privacy": round(assessment.privacy, 2),
                "security": round(assessment.security, 2),
                "cost": round(assessment.cost, 2),
                "irreversibility": round(assessment.irreversibility, 2),
            },
            "reasons": assessment.reasons,
            "recommendations": assessment.recommendations,
        }

    def _score_action(self, action: str, context: Dict) -> RiskAssessment:
        """Score an action across each risk category using keyword rules."""
        action_lower = action.lower()
        scores: Dict[str, float] = {}

        for category, levels in self._risk_rules.items():
            cat_score = 0.0
            for severity, patterns in levels.items():
                for pattern in patterns:
                    if re.search(pattern, action_lower):
                        if severity == "high":
                            cat_score = max(cat_score, 8.0)
                        elif severity == "medium":
                            cat_score = max(cat_score, 5.0)
                        else:
                            cat_score = max(cat_score, 2.5)

            # Context modifiers
            if context.get("target_sensitive_data"):
                if category in ("data_loss", "privacy"):
                    cat_score = min(cat_score + 2.0, 10.0)
            if context.get("user_confirmed"):
                cat_score = max(cat_score - 2.0, 0.0)
            if context.get("test_environment"):
                cat_score = max(cat_score - 1.5, 0.0)

            scores[category] = cat_score

        # Compute weighted overall
        overall = sum(
            scores.get(cat, 0.0) * weight
            for cat, weight in self._weights.items()
        )

        return RiskAssessment(
            data_loss=scores.get("data_loss", 0.0),
            privacy=scores.get("privacy", 0.0),
            security=scores.get("security", 0.0),
            cost=scores.get("cost", 0.0),
            irreversibility=scores.get("irreversibility", 0.0),
            overall_score=overall,
        )

    # ------------------------------------------------------------------
    # Recommendations & reasons
    # ------------------------------------------------------------------

    def _generate_recommendations(self, assessment: RiskAssessment) -> List[str]:
        """Generate actionable recommendations based on risk scores."""
        recs = []
        if assessment.data_loss >= 5.0:
            recs.append("Create a backup before proceeding with this action.")
        if assessment.privacy >= 5.0:
            recs.append(
                "Review data sharing permissions. "
                "Consider anonymizing sensitive information."
            )
        if assessment.security >= 5.0:
            recs.append(
                "This action has security implications. "
                "Request explicit user confirmation."
            )
        if assessment.cost >= 5.0:
            recs.append(
                "This action may incur costs. "
                "Confirm budget allocation first."
            )
        if assessment.irreversibility >= 5.0:
            recs.append(
                "This action is hard to undo. "
                "Consider a dry-run or staging environment first."
            )
        if not recs and assessment.overall_score < 3.0:
            recs.append("Action appears safe to proceed.")
        return recs

    def _build_reasons(self, action: str, assessment: RiskAssessment) -> List[str]:
        """Build human-readable reasons for the risk scores."""
        reasons = []
        if assessment.data_loss >= 3.0:
            reasons.append(f"Data loss risk ({assessment.data_loss:.1f}/10)")
        if assessment.privacy >= 3.0:
            reasons.append(f"Privacy concern ({assessment.privacy:.1f}/10)")
        if assessment.security >= 3.0:
            reasons.append(f"Security risk ({assessment.security:.1f}/10)")
        if assessment.cost >= 3.0:
            reasons.append(f"Cost implication ({assessment.cost:.1f}/10)")
        if assessment.irreversibility >= 3.0:
            reasons.append(f"Irreversibility ({assessment.irreversibility:.1f}/10)")
        if not reasons:
            reasons.append("No significant risks detected.")
        return reasons

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def get_sentinel_report(self) -> str:
        """Return a summary report of sentinel activity."""
        total = len(self._assessment_history)
        blocked = len(self._blocked_actions)
        if total == 0:
            return "Sentinel: ACTIVE — No actions evaluated yet."

        avg_score = (
            sum(a.overall_score for a in self._assessment_history) / total
        )
        return (
            f"Sentinel: ACTIVE | "
            f"Evaluated: {total} | "
            f"Blocked: {blocked} | "
            f"Avg risk score: {avg_score:.1f}/10"
        )

    def get_assessment_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Return recent assessment history."""
        return [
            {
                "classification": a.classification.value,
                "overall_score": round(a.overall_score, 2),
                "reasons": a.reasons,
            }
            for a in self._assessment_history[-limit:]
        ]
