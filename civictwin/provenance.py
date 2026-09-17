from __future__ import annotations

from enum import Enum


class Provenance(str, Enum):
    """Where a piece of data came from.

    The three original classes describe an *asset*: whether it was taken from a
    source, derived from one, or invented by us. The wider vocabulary in
    `Evidence` describes an *input to a result*, which is a different question:
    a synthetic asset can still carry a verified topology, and an observed asset
    can carry an assumed fragility.
    """

    OBSERVED = "observed"
    INFERRED = "inferred"
    SYNTHETIC = "synthetic"


class Evidence(str, Enum):
    """How much an input to a result can be relied on.

    Ordered from firmest to weakest, so a result's overall standing is the
    weakest evidence it rests on.
    """

    OBSERVED = "observed"
    VERIFIED = "verified"
    ESTIMATED = "estimated"
    ASSUMED = "assumed"
    SIMULATED = "simulated"
    EXTERNAL_MODEL = "external_model"


#: Firmest first. A claim is only as good as the weakest thing under it.
EVIDENCE_ORDER: tuple[Evidence, ...] = (
    Evidence.OBSERVED,
    Evidence.VERIFIED,
    Evidence.ESTIMATED,
    Evidence.EXTERNAL_MODEL,
    Evidence.SIMULATED,
    Evidence.ASSUMED,
)


def weakest(items: "list[Evidence] | tuple[Evidence, ...]") -> Evidence:
    """The least reliable evidence in a set, which is what governs the claim."""
    if not items:
        return Evidence.ASSUMED
    return max(items, key=EVIDENCE_ORDER.index)
