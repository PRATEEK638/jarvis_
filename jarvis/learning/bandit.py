"""Route selection as a multi-armed bandit (vision packs 41, 42, 72).

The router currently orders candidates by a hand-written quality number I
assigned. That number is a guess, and it never changes however the routes
actually perform on this machine, on these tasks, over this network.

This replaces the guess with measurement, using Thompson sampling - the same
technique quantitative trading and clinical trials use for the identical
problem: repeatedly choose among options with unknown, noisy payoffs, while
still occasionally testing the ones you have written off.

Why Thompson sampling rather than the obvious alternatives:

  greedy ("always use whatever won last")
      never revisits a route that had one bad night, and a rate-limited
      provider looks permanently broken after a single 429.

  epsilon-greedy ("explore 10% of the time at random")
      explores at a fixed rate forever, wasting calls on a route already known
      to be poor, and explores uniformly instead of preferring the plausible
      contenders.

  Thompson sampling
      samples each route's success rate from its posterior and picks the
      winner. A route with 2 wins from 2 tries has a wide posterior and gets
      tried again; one with 3 wins from 60 tries has a narrow one and is
      quietly dropped. Exploration falls off automatically as evidence
      accumulates, with no tuning parameter.

Context matters, so statistics are kept per (route, task kind) rather than per
route: the fast small model may be perfectly good at simple file requests and
useless at diagnosis, and a single average hides exactly that.

Crucially this only *orders candidates the router already allows*. Privacy
pinning, availability and the circuit breaker are hard constraints and are
applied before this is consulted - a bandit must never be able to learn its
way around a safety rule.
"""

from __future__ import annotations

import json
import math
import random
import time
from dataclasses import dataclass, field
from pathlib import Path

from jarvis.config import settings
from jarvis.core.events import emit

STATS_PATH = settings.DATA_DIR / "route_stats.json"

# Beta(1,1) is a uniform prior: before any evidence, every route is equally
# plausible. Deliberately not optimistic - an untried route should be explored
# because it is unknown, not because it is assumed good.
PRIOR_ALPHA = 1.0
PRIOR_BETA = 1.0

# Latency is a real cost, but a wrong answer delivered quickly is worthless, so
# speed only breaks ties between routes of similar quality.
LATENCY_WEIGHT = 0.15
REFERENCE_MS = 8000.0

# An arm with fewer trials than this is tried before the posterior is trusted.
# Small on purpose: enough to give a new provider a fair hearing, few enough
# that a genuinely poor one is quickly demoted by the evidence it generates.
MIN_TRIALS = 3


@dataclass
class Arm:
    """One route's record for one kind of task."""

    successes: float = 0.0
    failures: float = 0.0
    total_ms: float = 0.0
    calls: int = 0

    @property
    def trials(self) -> float:
        return self.successes + self.failures

    @property
    def rate(self) -> float:
        if self.trials == 0:
            return 0.5
        return self.successes / self.trials

    @property
    def avg_ms(self) -> float:
        return self.total_ms / self.calls if self.calls else REFERENCE_MS

    def sample(self, rng: random.Random) -> float:
        """Draw a plausible success rate from this arm's posterior."""
        return rng.betavariate(PRIOR_ALPHA + self.successes,
                               PRIOR_BETA + self.failures)


def _key(route_id: str, kind: str) -> str:
    return f"{route_id}|{kind}"


class RouteBandit:
    """Learns which route to prefer, per kind of task, from real outcomes."""

    def __init__(self, path: Path | None = None, seed: int | None = None) -> None:
        self._path = path or STATS_PATH
        self._arms: dict[str, Arm] = {}
        self._rng = random.Random(seed)
        self._load()

    # -- persistence ---------------------------------------------------------

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return                       # corrupt stats must not break routing
        for key, data in (raw.get("arms") or {}).items():
            self._arms[key] = Arm(
                successes=float(data.get("successes", 0)),
                failures=float(data.get("failures", 0)),
                total_ms=float(data.get("total_ms", 0)),
                calls=int(data.get("calls", 0)))

    def save(self) -> None:
        payload = {
            "updated": time.time(),
            "arms": {k: {"successes": round(a.successes, 3),
                         "failures": round(a.failures, 3),
                         "total_ms": round(a.total_ms), "calls": a.calls}
                     for k, a in self._arms.items()},
        }
        try:
            self._path.write_text(json.dumps(payload, indent=1),
                                  encoding="utf-8")
        except OSError:
            pass

    # -- learning ------------------------------------------------------------

    def record(self, route_id: str, kind: str, *, success: bool,
               latency_ms: float) -> None:
        arm = self._arms.setdefault(_key(route_id, kind), Arm())
        if success:
            arm.successes += 1
        else:
            arm.failures += 1
        arm.total_ms += max(0.0, latency_ms)
        arm.calls += 1
        # emit()'s first parameter is itself called `kind`, so the task kind
        # has to travel under a different name.
        emit("bandit.observed", route=route_id, task_kind=kind,
             success=success, rate=round(arm.rate, 3), trials=int(arm.trials))
        self.save()

    def decay(self, factor: float = 0.98) -> None:
        """Age the evidence slightly.

        Providers change: a model is swapped, a free tier is throttled, a
        network moves. Without decay a route's first hundred results dominate
        forever and the bandit stops being able to notice that the world moved.
        """
        for arm in self._arms.values():
            arm.successes *= factor
            arm.failures *= factor

    # -- choosing ------------------------------------------------------------

    def order(self, route_ids: list[str], kind: str) -> list[str]:
        """Rank the given routes best-first for this kind of task.

        The input is already filtered for privacy, availability and the circuit
        breaker. This only decides the order among what is permitted.
        """
        if len(route_ids) <= 1:
            return list(route_ids)

        # Cold start. Pure Thompson sampling has a real failure here, measured
        # rather than assumed: against an arm with 60 wins, a brand-new arm was
        # sampled 0% of the time across 400 draws. A provider added later would
        # therefore never be discovered, however good it was.
        #
        # So an arm below MIN_TRIALS is ordered first. This is bounded and
        # self-terminating - once it has that little evidence, the posterior
        # takes over permanently - which keeps it from becoming a tuning knob.
        untried = [rid for rid in route_ids
                   if self._arms.get(_key(rid, kind), Arm()).trials < MIN_TRIALS]
        rest = [rid for rid in route_ids if rid not in set(untried)]

        scored = []
        for rid in rest:
            arm = self._arms.get(_key(rid, kind), Arm())
            quality = arm.sample(self._rng)
            # Latency discount, bounded so a fast-but-wrong route can never
            # outrank a slow-but-right one on speed alone.
            penalty = LATENCY_WEIGHT * math.tanh(arm.avg_ms / REFERENCE_MS)
            scored.append((quality - penalty, rid))
        scored.sort(reverse=True)

        # Least-tried first among the cold arms, so they warm up evenly.
        untried.sort(key=lambda r: self._arms.get(_key(r, kind), Arm()).trials)
        return untried + [rid for _, rid in scored]

    # -- reporting -----------------------------------------------------------

    def report(self) -> list[dict[str, object]]:
        out = []
        for key, arm in sorted(self._arms.items(),
                               key=lambda kv: -kv[1].trials):
            route, _, kind = key.partition("|")
            out.append({
                "route": route, "kind": kind, "trials": round(arm.trials, 1),
                "success_rate": round(arm.rate, 3),
                "avg_ms": round(arm.avg_ms),
                # A wide interval means "not enough evidence yet", which is
                # more useful to a reader than the point estimate alone.
                "confident": arm.trials >= 8,
            })
        return out
