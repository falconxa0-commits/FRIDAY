import time
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime
from collections import Counter

logger = logging.getLogger("QuantumContinuum")


class QuantumContinuum:
    """Predicts user's next intent using n-gram pattern matching and
    time-of-day analysis on the sequence of past intents."""

    def __init__(self, memory=None):
        self.memory = memory
        self.timeline: Dict[str, Any] = {
            "past": [],
            "present": None,
            "predicted_future": [],
        }
        # Intent sequence for n-gram analysis
        self._intent_sequence: List[str] = []
        self._intent_timestamps: List[float] = []
        # N-gram model: bigram counts  (prev_intent -> Counter of next_intent)
        self._bigram_counts: Dict[str, Counter] = {}
        # Time-of-day intent counts: hour -> Counter of intents
        self._tod_counts: Dict[int, Counter] = {}
        self._max_history = 500

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def update_present(self, state: str):
        """Record a new intent / state from the user."""
        entry = {
            "state": state,
            "timestamp": time.time(),
            "datetime": datetime.utcnow().isoformat(),
        }
        self.timeline["present"] = entry
        self.timeline["past"].append(entry)
        if len(self.timeline["past"]) > self._max_history:
            self.timeline["past"] = self.timeline["past"][-self._max_history:]

        # Update n-gram model
        self._record_intent(state)

    def _record_intent(self, intent: str):
        """Feed an intent into the n-gram and time-of-day models."""
        now = time.time()
        hour = datetime.utcnow().hour

        # Bigram update
        if self._intent_sequence:
            prev = self._intent_sequence[-1]
            if prev not in self._bigram_counts:
                self._bigram_counts[prev] = Counter()
            self._bigram_counts[prev][intent] += 1

        self._intent_sequence.append(intent)
        self._intent_timestamps.append(now)

        # Time-of-day update
        if hour not in self._tod_counts:
            self._tod_counts[hour] = Counter()
        self._tod_counts[hour][intent] += 1

        # Trim
        if len(self._intent_sequence) > self._max_history:
            self._intent_sequence = self._intent_sequence[-self._max_history:]
            self._intent_timestamps = self._intent_timestamps[-self._max_history:]

    # ------------------------------------------------------------------
    # Prediction
    # ------------------------------------------------------------------

    def predict_next_intent(self, user_id: str = "default") -> Dict[str, Any]:
        """Predict the user's next intent with confidence scoring.

        Returns a dict with:
            - prediction: the most likely next intent
            - confidence: 0.0 - 1.0
            - alternatives: list of (intent, probability) runners-up
            - method: which model produced the prediction
        """
        predictions: Dict[str, float] = {}

        # 1. Bigram-based prediction
        if self._intent_sequence:
            last_intent = self._intent_sequence[-1]
            bigram_pred = self._predict_bigram(last_intent)
            for intent, score in bigram_pred.items():
                predictions[intent] = predictions.get(intent, 0.0) + score * 0.6

        # 2. Time-of-day prediction
        hour = datetime.utcnow().hour
        tod_pred = self._predict_time_of_day(hour)
        for intent, score in tod_pred.items():
            predictions[intent] = predictions.get(intent, 0.0) + score * 0.4

        if not predictions:
            result = {
                "prediction": "general_assistance",
                "confidence": 0.1,
                "alternatives": [],
                "method": "default",
            }
            self.timeline["predicted_future"].append(result)
            return result

        # Rank
        ranked = sorted(predictions.items(), key=lambda x: x[1], reverse=True)
        top_intent = ranked[0][0]
        total = sum(s for _, s in ranked)
        confidence = round(ranked[0][1] / total, 2) if total > 0 else 0.1

        # Determine dominant method
        method = "combined"
        if not self._intent_sequence and tod_pred:
            method = "time_of_day"
        elif self._intent_sequence and not tod_pred:
            method = "bigram"

        alternatives = [
            (intent, round(score / total, 2))
            for intent, score in ranked[1:4]
        ] if total > 0 else []

        result = {
            "prediction": top_intent,
            "confidence": confidence,
            "alternatives": alternatives,
            "method": method,
        }
        self.timeline["predicted_future"].append(result)
        return result

    def _predict_bigram(self, last_intent: str) -> Dict[str, float]:
        """Predict next intent from bigram counts."""
        if last_intent not in self._bigram_counts:
            return {}
        counter = self._bigram_counts[last_intent]
        total = sum(counter.values())
        if total == 0:
            return {}
        return {intent: count / total for intent, count in counter.items()}

    def _predict_time_of_day(self, hour: int) -> Dict[str, float]:
        """Predict intent from time-of-day patterns."""
        # Check current hour +/- 1 for broader patterns
        combined = Counter()
        for h in [hour - 1, hour, hour + 1]:
            if h in self._tod_counts:
                combined.update(self._tod_counts[h])
        total = sum(combined.values())
        if total == 0:
            return {}
        return {intent: count / total for intent, count in combined.items()}

    # ------------------------------------------------------------------
    # Contextual map
    # ------------------------------------------------------------------

    def get_contextual_map(self) -> Dict[str, Any]:
        """Return the full timeline state including prediction metadata."""
        return {
            "past_count": len(self.timeline["past"]),
            "present": self.timeline["present"],
            "recent_predictions": self.timeline["predicted_future"][-5:],
            "unique_intents_tracked": len(set(self._intent_sequence)),
            "bigram_vocabulary": list(self._bigram_counts.keys()),
        }

    def get_prediction_stats(self) -> Dict[str, Any]:
        """Return statistics about the prediction model."""
        return {
            "total_intents_recorded": len(self._intent_sequence),
            "bigram_transitions": sum(
                len(c) for c in self._bigram_counts.values()
            ),
            "tod_hours_tracked": len(self._tod_counts),
            "most_common_intent": (
                Counter(self._intent_sequence).most_common(1)[0][0]
                if self._intent_sequence else None
            ),
        }
