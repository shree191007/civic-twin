"""The assumption ledger: why the model thinks what it thinks.

Every headline result rests on a stack of inputs of wildly different quality —
an observed bridge, an inferred tower position, an assumed fragility curve, a
restoration time estimated from history. The ledger names them, so a reader can
see what the answer is standing on before deciding how much weight to put on it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from civictwin.config import Config
from civictwin.hazard.schemas.forecast import HazardForecast, UncertaintySource
from civictwin.ontology import AssetKind, Township
from civictwin.provenance import Evidence, Provenance, weakest

#: Asset provenance maps onto evidence for topology questions.
PROVENANCE_EVIDENCE: dict[Provenance, Evidence] = {
    Provenance.OBSERVED: Evidence.OBSERVED,
    Provenance.INFERRED: Evidence.ESTIMATED,
    Provenance.SYNTHETIC: Evidence.ASSUMED,
}


@dataclass(slots=True)
class Assumption:
    """One input, what it is, and how much it can be relied on."""

    subject: str
    statement: str
    evidence: Evidence
    source: str
    uncertainty: UncertaintySource | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "statement": self.statement,
            "evidence": self.evidence.value,
            "source": self.source,
            "uncertainty": None if self.uncertainty is None else self.uncertainty.value,
        }


@dataclass(slots=True)
class Ledger:
    """The assumptions behind one result."""

    claim: str
    assumptions: list[Assumption] = field(default_factory=list)

    @property
    def standing(self) -> Evidence:
        """The weakest evidence under the claim, which is what governs it."""
        return weakest([a.evidence for a in self.assumptions])

    def to_dict(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for a in self.assumptions:
            counts[a.evidence.value] = counts.get(a.evidence.value, 0) + 1
        return {
            "claim": self.claim,
            "standing": self.standing.value,
            "counts": counts,
            "assumptions": [a.to_dict() for a in self.assumptions],
        }


def for_asset(township: Township, asset_id: str, cfg: Config) -> Ledger:
    """Why the model believes what it does about one asset."""
    asset = township.assets[asset_id]
    ledger = Ledger(claim=f"What {asset_id} ({asset.name}) is and what it carries")

    ledger.assumptions.append(
        Assumption(
            subject="existence and position",
            statement=f"{asset_id} is a {asset.kind.value} at this location",
            evidence=PROVENANCE_EVIDENCE[asset.provenance],
            source=f"{asset.provenance.value} data",
        )
    )
    providers = township.providers(asset_id)
    if providers:
        link_evidence = [
            PROVENANCE_EVIDENCE[ln.provenance]
            for ln in township.links
            if ln.target == asset_id
        ]
        ledger.assumptions.append(
            Assumption(
                subject="dependencies",
                statement=(
                    f"{asset_id} depends on {', '.join(sorted(providers))}"
                ),
                evidence=weakest(link_evidence),
                source="network topology",
                uncertainty=UncertaintySource.DEPENDENCY_TOPOLOGY,
            )
        )
    ledger.assumptions.append(
        Assumption(
            subject="flood vulnerability",
            statement=(
                f"{asset_id} reaches moderate damage at about "
                f"{asset.fragility_median_m:.1f} m of standing water"
            ),
            evidence=Evidence.ASSUMED,
            source="fragility curve by asset class, after Hazus-style tables",
            uncertainty=UncertaintySource.ASSET_VULNERABILITY,
        )
    )
    ledger.assumptions.append(
        Assumption(
            subject="restoration time",
            statement=(
                f"repairing {asset_id} takes about {asset.repair_hours_base:.0f} h "
                "at moderate damage, longer if the site is still wet"
            ),
            evidence=Evidence.ESTIMATED,
            source="historical repair durations by asset class",
            uncertainty=UncertaintySource.RESTORATION_TIME,
        )
    )
    if asset.backup_hours or asset.fuel_hours:
        ledger.assumptions.append(
            Assumption(
                subject="autonomy",
                statement=(
                    f"{asset_id} runs for {asset.backup_hours:.0f} h on battery and "
                    f"{asset.fuel_hours:.0f} h on stored fuel, with no recharge "
                    "inside the horizon"
                ),
                evidence=Evidence.ASSUMED,
                source="design standard for the asset class",
            )
        )
    if asset.kind in (AssetKind.HOSPITAL, AssetKind.CLINIC):
        ledger.assumptions.append(
            Assumption(
                subject="service model",
                statement=(
                    "care capability is modelled as availability, not beds or staffing"
                ),
                evidence=Evidence.ASSUMED,
                source="model simplification",
            )
        )
    del cfg
    return ledger


def for_population(township: Township) -> Assumption:
    return Assumption(
        subject="population exposure",
        statement=(
            f"{sum(z.population for z in township.zones):,} people across "
            f"{len(township.zones)} zones, distributed by zone centroid"
        ),
        evidence=Evidence.ESTIMATED,
        source="zone population estimates",
        uncertainty=UncertaintySource.POPULATION_EXPOSURE,
    )


def for_result(
    township: Township,
    cfg: Config,
    claim: str,
    assets: Sequence[str] = (),
    forecast: HazardForecast | None = None,
) -> Ledger:
    """The ledger behind a headline number, such as a risk figure or a plan."""
    ledger = Ledger(claim=claim)
    if forecast is not None:
        ledger.assumptions.append(
            Assumption(
                subject="hazard",
                statement=(
                    f"{forecast.hazard_type.value.replace('_', ' ')} of "
                    f"{forecast.severity_range[0]:.0f}-{forecast.severity_range[1]:.0f} mm, "
                    f"probability {forecast.probability:.0%}"
                ),
                evidence=Evidence.EXTERNAL_MODEL,
                source=forecast.source,
                uncertainty=UncertaintySource.HAZARD_INTENSITY,
            )
        )
    else:
        ledger.assumptions.append(
            Assumption(
                subject="hazard",
                statement=(
                    "rainfall drawn from a Gumbel distribution fitted to the "
                    "local record, one storm per scenario year"
                ),
                evidence=Evidence.ESTIMATED,
                source="configured hazard distribution",
                uncertainty=UncertaintySource.HAZARD_INTENSITY,
            )
        )
    ledger.assumptions.append(for_population(township))
    ledger.assumptions.append(
        Assumption(
            subject="cascade",
            statement=(
                "failures propagate along the modelled dependencies in hourly "
                "steps over 72 hours"
            ),
            evidence=Evidence.SIMULATED,
            source="civic-twin cascade engine",
        )
    )
    ledger.assumptions.append(
        Assumption(
            subject="operator behaviour",
            statement=(
                "crews follow a fixed dispatch rule; de-energisation follows a "
                "flood-fraction threshold"
            ),
            evidence=Evidence.ASSUMED,
            source="rule-based operator model",
            uncertainty=UncertaintySource.OPERATOR_BEHAVIOUR,
        )
    )
    counted: set[str] = set()
    for asset_id in assets:
        if asset_id in counted or asset_id not in township.assets:
            continue
        counted.add(asset_id)
        asset = township.assets[asset_id]
        ledger.assumptions.append(
            Assumption(
                subject=f"{asset_id} topology",
                statement=(
                    f"{asset_id} ({asset.name}) and its connections are "
                    f"{asset.provenance.value} data"
                ),
                evidence=PROVENANCE_EVIDENCE[asset.provenance],
                source=f"{asset.provenance.value} layer",
                uncertainty=UncertaintySource.DEPENDENCY_TOPOLOGY,
            )
        )
    del cfg
    return ledger


def provenance_summary(township: Township) -> dict[str, Any]:
    """How much of the model is observed, inferred and invented."""
    counts: dict[str, int] = {}
    by_portfolio: dict[str, dict[str, int]] = {}
    for asset in township.assets.values():
        counts[asset.provenance.value] = counts.get(asset.provenance.value, 0) + 1
        portfolio = by_portfolio.setdefault(asset.portfolio.value, {})
        portfolio[asset.provenance.value] = portfolio.get(asset.provenance.value, 0) + 1
    total = sum(counts.values())
    return {
        "counts": counts,
        "by_portfolio": by_portfolio,
        "total": total,
        "sentence": (
            f"{counts.get('observed', 0)} assets observed, "
            f"{counts.get('inferred', 0)} inferred, "
            f"{counts.get('synthetic', 0)} synthetic"
        ),
    }
