"""Recursive self-improvement module (stub).

Previously contained the RecursiveModificationProtocol class.
That functionality has been removed as it posed an unacceptable
risk of autonomous code modification without human review.

All core changes must go through the standard review process.
"""

import logging

logger = logging.getLogger(__name__)


class RecursiveModificationProtocol:
    """Stub — autonomous code modification is no longer supported.

    All changes to FRIDAY's core modules must be proposed and reviewed
    by a human operator.  This class is retained for import compatibility.
    """

    def __init__(self, brain=None):
        self.brain = brain
        logger.warning(
            "RecursiveModificationProtocol is a stub — "
            "autonomous code modification has been disabled."
        )

    async def propose_core_improvement(self, target_file):
        """Disabled — returns a message indicating the feature is no longer available."""
        return (
            "Autonomous code modification has been disabled for safety. "
            "Please propose changes through the standard review process."
        )
