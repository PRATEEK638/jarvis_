"""A trained ability classifier — JARVIS learning from its own use.

What this is: a supervised model that maps a request to the ability that has
actually served that kind of request before, trained on the event log.

Why it is worth having, given a 550B model is one API call away:

  - it is free and instant, where the API call costs a second or more
  - it is private: nothing leaves the machine
  - it improves every time JARVIS is used, because every run adds an example
  - it degrades to the LLM rather than guessing, so a wrong prediction costs
    nothing

Why a linear model over character n-grams rather than a neural network: with
~180 examples a neural network memorises and reports a flattering accuracy that
collapses on anything new. Character n-grams also handle the actual variation
in real requests - "foldr", "make a dir", "create folder" - without needing a
vocabulary. TF-IDF plus logistic regression is the honest choice at this data
size, and it trains in under a second.

The confidence gate is the important part. A classifier that answers everything
is worse than useless here, because a confidently wrong ability runs the wrong
action on a real machine. Below the threshold this abstains and the planner
takes over.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from jarvis.config import settings
from jarvis.core.events import emit
from jarvis.learning import dataset

MODEL_PATH = settings.DATA_DIR / "ability_classifier.joblib"
CARD_PATH = settings.DATA_DIR / "ability_classifier.json"

# Below this probability the model abstains. Chosen from measurement, not
# taste: at 0.50 the model was 100% correct on 91% of held-out requests, at
# 0.62 on 80%, and at 0.75 on only 41% - so 0.75 was abstaining on requests it
# would have got right, which just spends an API call for nothing.
DEFAULT_THRESHOLD = 0.50
MIN_EXAMPLES = 40


@dataclass
class TrainReport:
    trained: bool
    reason: str = ""
    examples: int = 0
    classes: int = 0
    accuracy: float = 0.0
    accuracy_at_threshold: float = 0.0
    coverage_at_threshold: float = 0.0
    threshold: float = DEFAULT_THRESHOLD
    per_class: dict[str, int] = field(default_factory=dict)
    trained_at: float = 0.0

    def describe(self) -> str:
        if not self.trained:
            return f"not trained: {self.reason}"
        return (f"{self.examples} examples, {self.classes} abilities · "
                f"held-out accuracy {self.accuracy:.0%} · "
                f"at threshold {self.threshold:.2f}: "
                f"{self.accuracy_at_threshold:.0%} correct on "
                f"{self.coverage_at_threshold:.0%} of requests")


def _build_pipeline():
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline

    return Pipeline([
        # Character n-grams, not words: real requests contain typos and
        # inconsistent phrasing, and sub-word overlap survives both.
        ("tfidf", TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5),
                                  sublinear_tf=True, min_df=1)),
        ("clf", LogisticRegression(max_iter=2000, C=4.0,
                                   class_weight="balanced")),
    ])


def train(*, threshold: float = DEFAULT_THRESHOLD,
          log: Path | None = None) -> TrainReport:
    """Train on the event log and write the model plus an honest model card."""
    raw = dataset.build(log)
    examples, counts = dataset.usable(raw)
    if len(examples) < MIN_EXAMPLES:
        return TrainReport(trained=False, examples=len(examples),
                           reason=f"only {len(examples)} usable examples; "
                                  f"need {MIN_EXAMPLES}")
    labels = sorted({e.ability for e in examples})
    if len(labels) < 2:
        return TrainReport(trained=False, examples=len(examples),
                           reason="fewer than two ability classes")

    from sklearn.model_selection import train_test_split
    import joblib

    X = [e.text for e in examples]
    y = [e.ability for e in examples]

    # Stratify so every ability appears in both halves; without it a rare class
    # lands entirely in test and accuracy becomes noise.
    X_tr, X_te, y_tr, y_te = train_test_split(
        X, y, test_size=0.25, random_state=0, stratify=y)

    pipe = _build_pipeline()
    pipe.fit(X_tr, y_tr)

    probs = pipe.predict_proba(X_te)
    classes = list(pipe.named_steps["clf"].classes_)
    correct = confident = confident_correct = 0
    for row, truth in zip(probs, y_te):
        best_i = max(range(len(row)), key=lambda i: row[i])
        predicted, p = classes[best_i], row[best_i]
        correct += predicted == truth
        if p >= threshold:
            confident += 1
            confident_correct += predicted == truth

    report = TrainReport(
        trained=True, examples=len(examples), classes=len(labels),
        accuracy=correct / max(1, len(y_te)),
        accuracy_at_threshold=confident_correct / max(1, confident),
        coverage_at_threshold=confident / max(1, len(y_te)),
        threshold=threshold, per_class=counts, trained_at=time.time(),
    )

    # Retrain on everything for the shipped model: the split existed only to
    # measure, and throwing away a quarter of 184 examples is wasteful.
    final = _build_pipeline()
    final.fit(X, y)
    joblib.dump(final, MODEL_PATH)
    CARD_PATH.write_text(json.dumps({
        "examples": report.examples, "classes": report.classes,
        "held_out_accuracy": round(report.accuracy, 4),
        "accuracy_at_threshold": round(report.accuracy_at_threshold, 4),
        "coverage_at_threshold": round(report.coverage_at_threshold, 4),
        "threshold": threshold, "trained_at": report.trained_at,
        "per_class": report.per_class,
        "note": "Trained on JARVIS's own event log. Abstains below the "
                "threshold so the planner handles anything uncertain.",
    }, indent=1), encoding="utf-8")
    emit("model.trained", examples=report.examples, classes=report.classes,
         accuracy=round(report.accuracy, 3))
    return report


class AbilityClassifier:
    """Loads the trained model, and abstains when unsure."""

    def __init__(self, threshold: float = DEFAULT_THRESHOLD) -> None:
        self.threshold = threshold
        self._pipe = None
        self._card: dict = {}
        self._load()

    def _load(self) -> None:
        if not MODEL_PATH.exists():
            return
        try:
            import joblib
            self._pipe = joblib.load(MODEL_PATH)
            if CARD_PATH.exists():
                self._card = json.loads(CARD_PATH.read_text(encoding="utf-8"))
                self.threshold = float(self._card.get("threshold",
                                                      self.threshold))
        except Exception as exc:      # noqa: BLE001 - a bad model must not break JARVIS
            emit("model.load_failed", error=str(exc))
            self._pipe = None

    @property
    def available(self) -> bool:
        return self._pipe is not None

    def predict(self, text: str) -> tuple[str, float] | None:
        """The ability for this request, or None when not confident enough."""
        if self._pipe is None or not (text or "").strip():
            return None
        try:
            row = self._pipe.predict_proba([text])[0]
        except Exception:             # noqa: BLE001
            return None
        classes = list(self._pipe.named_steps["clf"].classes_)
        best_i = max(range(len(row)), key=lambda i: row[i])
        # str() matters: sklearn hands back numpy.str_, which leaks into
        # event logs and JSON as "np.str_('open_app')".
        ability, p = str(classes[best_i]), float(row[best_i])
        if p < self.threshold:
            return None
        return ability, p

    def card(self) -> dict:
        return dict(self._card)
