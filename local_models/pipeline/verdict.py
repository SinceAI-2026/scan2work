"""The contract every signal module (appearance, plausibility, detector, consistency) keeps: an
instance goes in, a Signal comes out. pipeline/fuse.py reads them in a fixed order."""
from typing import NamedTuple


class Signal(NamedTuple):
    verdict: str  # what the signal says: a label, "fits", "agree", ...
    score: float  # 0-1, how strongly it says it
    reason: str  # a few words a person can check, e.g. "6/7 views agree"

    def json(self):
        return {"verdict": self.verdict, "score": round(float(self.score), 3), "reason": self.reason}
