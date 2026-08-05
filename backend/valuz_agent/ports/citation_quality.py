"""Trusted, layered citation quality policies.

OSS owns the baseline policy and the ordered merge contract. Commercial and
distribution overlays register additive providers in fixed slots; a later
provider can tighten the effective policy, but cannot replace an earlier
layer or disable an OSS invariant.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Protocol, cast

import yaml

CitationPolicyMode = Literal["required-on-evidence", "strict-domain"]
CitationPolicyLayer = Literal["oss", "commercial", "distribution"]
CitationPolicySnapshotLayer = CitationPolicyLayer | Literal["effective"]

_LAYER_ORDER: tuple[CitationPolicyLayer, ...] = (
    "oss",
    "commercial",
    "distribution",
)
_MODE_STRENGTH: dict[CitationPolicyMode, int] = {
    "required-on-evidence": 0,
    "strict-domain": 1,
}
_MAX_POLICY_BYTES = 128_000
_TASK_COVERAGE_SECTIONS = {"revision", "review_guidance", "evaluation"}
_TASK_COVERAGE_GUIDANCE_KEYS = {
    "material_gap_types",
    "completion_dimensions",
    "source_boundary_notes",
    "supplement_rules",
}
_TASK_COVERAGE_SUPPLEMENT_RULES = {
    "append_only",
    "do_not_repeat_completed_content",
    "preserve_visible_history",
}
_OSS_POLICY_PATH = (
    Path(__file__).resolve().parents[1] / "resources" / "citation-policies" / "oss" / "policy.yaml"
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CitationQualityPolicySnapshot:
    policy_id: str
    revision: str
    mode: CitationPolicyMode
    config: dict[str, Any]
    layer: CitationPolicySnapshotLayer = "distribution"
    layers: tuple[dict[str, str], ...] = field(default_factory=tuple)
    unavailable_layers: tuple[CitationPolicyLayer, ...] = field(default_factory=tuple)

    def session_metadata(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "policy_id": self.policy_id,
            "revision": self.revision,
            "mode": self.mode,
            "config": copy.deepcopy(self.config),
        }
        if self.layers:
            payload["layers"] = copy.deepcopy(list(self.layers))
        if self.unavailable_layers:
            payload["unavailable_layers"] = list(self.unavailable_layers)
        # Fail at the trusted host boundary instead of sending an
        # unserializable or unbounded object into a remote kernel.
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > _MAX_POLICY_BYTES:
            raise ValueError("citation quality policy exceeds 128 KiB")
        return payload


class CitationQualityPolicyPort(Protocol):
    async def resolve(
        self,
        user_id: str,
        *,
        session_metadata: dict[str, Any],
    ) -> CitationQualityPolicySnapshot | None: ...


class PackagedCitationQualityPolicy:
    """Load one immutable policy pack shipped with a trusted distribution."""

    def __init__(
        self,
        path: Path,
        *,
        policy_id: str,
        layer: CitationPolicyLayer,
        revision_prefix: str,
    ) -> None:
        self._path = path
        self._policy_id = policy_id
        self._layer = layer
        self._revision_prefix = revision_prefix

    def load(self) -> dict[str, Any]:
        return load_citation_policy_document(
            self._path,
            expected_policy_id=self._policy_id,
            expected_layer=self._layer,
            revision_prefix=self._revision_prefix,
        )

    async def resolve(
        self,
        user_id: str,
        *,
        session_metadata: dict[str, Any],
    ) -> CitationQualityPolicySnapshot:
        if not user_id:
            raise ValueError("user_id is required")
        policy = self.load()
        return citation_policy_snapshot_from_document(
            policy,
            session_metadata=session_metadata,
        )


class CitationQualityPolicyRegistry:
    """Fixed-order policy registry with monotonic merge semantics."""

    def __init__(self, *, oss_provider: CitationQualityPolicyPort | None = None) -> None:
        self._providers: dict[CitationPolicyLayer, CitationQualityPolicyPort] = {
            "oss": oss_provider
            or PackagedCitationQualityPolicy(
                _OSS_POLICY_PATH,
                policy_id="oss-citation-baseline",
                layer="oss",
                revision_prefix="citation-baseline-v",
            )
        }

    def register(
        self,
        layer: Literal["commercial", "distribution"],
        provider: CitationQualityPolicyPort,
    ) -> None:
        if layer in self._providers:
            raise RuntimeError(f"citation quality policy layer already registered: {layer}")
        self._providers[layer] = provider

    def unregister(self, layer: Literal["commercial", "distribution"]) -> None:
        """Remove an overlay layer during explicit lifecycle teardown/tests."""

        self._providers.pop(layer, None)

    def provider(self, layer: CitationPolicyLayer) -> CitationQualityPolicyPort | None:
        return self._providers.get(layer)

    async def resolve(
        self,
        user_id: str,
        *,
        session_metadata: dict[str, Any],
    ) -> CitationQualityPolicySnapshot:
        snapshots: list[CitationQualityPolicySnapshot] = []
        unavailable: list[CitationPolicyLayer] = []
        for layer in _LAYER_ORDER:
            provider = self._providers.get(layer)
            if provider is None:
                continue
            try:
                snapshot = await provider.resolve(
                    user_id,
                    session_metadata=session_metadata,
                )
                if snapshot is None:
                    raise RuntimeError("registered provider returned no snapshot")
                if snapshot.layer != layer:
                    raise RuntimeError(
                        f"citation policy layer mismatch: registered={layer} "
                        f"snapshot={snapshot.layer}"
                    )
            except Exception:
                if layer == "oss":
                    raise
                logger.exception("citation quality policy layer unavailable: %s", layer)
                unavailable.append(layer)
                continue
            snapshots.append(snapshot)

        if not snapshots or snapshots[0].layer != "oss":
            raise RuntimeError("OSS citation quality policy is unavailable")
        return merge_citation_quality_policy_snapshots(
            snapshots,
            unavailable_layers=unavailable,
        )


class NoopCitationQualityPolicy:
    """Compatibility noop for consumers that have not adopted the registry."""

    async def resolve(
        self,
        user_id: str,
        *,
        session_metadata: dict[str, Any],
    ) -> None:
        del user_id, session_metadata
        return None


def citation_policy_snapshot_from_document(
    policy: dict[str, Any],
    *,
    session_metadata: dict[str, Any],
) -> CitationQualityPolicySnapshot:
    activation = policy.get("activation")
    activation = activation if isinstance(activation, dict) else {}
    layer = cast(CitationPolicyLayer, policy["layer"])
    return CitationQualityPolicySnapshot(
        policy_id=str(policy["policy_id"]),
        revision=str(policy["version"]),
        mode=citation_policy_activation_mode(activation, session_metadata),
        config={
            key: copy.deepcopy(value)
            for key, value in policy.items()
            if key not in {"version", "policy_id", "layer"}
        },
        layer=layer,
    )


def citation_policy_activation_mode(
    activation: dict[str, Any],
    session_metadata: dict[str, Any],
) -> CitationPolicyMode:
    valuz = session_metadata.get("valuz")
    valuz = valuz if isinstance(valuz, dict) else {}
    research = valuz.get("document_research")
    if isinstance(research, dict) and research.get("purpose") == "document-research":
        document_mode = activation.get("document_research_mode")
        if document_mode in _MODE_STRENGTH:
            return cast(CitationPolicyMode, document_mode)
    creation = valuz.get("creation_context")
    creation = creation if isinstance(creation, dict) else {}
    task_type = creation.get("task_type") or creation.get("kind")
    task_modes = activation.get("task_types")
    if isinstance(task_modes, dict) and isinstance(task_type, str):
        selected = task_modes.get(task_type)
        if selected in _MODE_STRENGTH:
            return cast(CitationPolicyMode, selected)
    default = activation.get("default_mode")
    return cast(
        CitationPolicyMode,
        default if default in _MODE_STRENGTH else "required-on-evidence",
    )


def merge_citation_quality_policy_snapshots(
    snapshots: list[CitationQualityPolicySnapshot],
    *,
    unavailable_layers: list[CitationPolicyLayer] | None = None,
) -> CitationQualityPolicySnapshot:
    if not snapshots:
        raise ValueError("at least one citation policy snapshot is required")
    expected_order = [layer for layer in _LAYER_ORDER if any(s.layer == layer for s in snapshots)]
    actual_order = [snapshot.layer for snapshot in snapshots]
    if actual_order != expected_order or len(set(actual_order)) != len(actual_order):
        raise ValueError("citation policy snapshots must use unique fixed-order layers")

    config: dict[str, Any] = {}
    mode: CitationPolicyMode = "required-on-evidence"
    layers: list[dict[str, str]] = []
    for snapshot in snapshots:
        config = _monotonic_merge(config, snapshot.config)
        if _MODE_STRENGTH[snapshot.mode] > _MODE_STRENGTH[mode]:
            mode = snapshot.mode
        layers.append(
            {
                "layer": snapshot.layer,
                "policy_id": snapshot.policy_id,
                "revision": snapshot.revision,
                "status": "active",
            }
        )
    # Audit and Task Coverage are post-publication sidecars. Policy can
    # classify issues, but can never block, hide or repair Runtime output.
    failure = config.get("failure")
    if isinstance(failure, dict):
        failure.pop("repair_attempts", None)
        failure["publish_on_degraded"] = "ready"
    for layer in unavailable_layers or []:
        layers.append(
            {
                "layer": layer,
                "policy_id": "unavailable",
                "revision": "unavailable",
                "status": "unavailable",
            }
        )
    layers.sort(key=lambda item: _LAYER_ORDER.index(cast(CitationPolicyLayer, item["layer"])))
    revision_input = json.dumps(
        {"layers": layers, "mode": mode, "config": config},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    revision = f"citation-effective-{hashlib.sha256(revision_input.encode()).hexdigest()[:16]}"
    return CitationQualityPolicySnapshot(
        policy_id="effective-citation-policy",
        revision=revision,
        mode=mode,
        config=config,
        layer="effective",
        layers=tuple(layers),
        unavailable_layers=tuple(unavailable_layers or ()),
    )


def _monotonic_merge(base: Any, addition: Any, path: tuple[str, ...] = ()) -> Any:
    if isinstance(base, dict) and isinstance(addition, dict):
        result = copy.deepcopy(base)
        for key, value in addition.items():
            result[key] = (
                _monotonic_merge(result[key], value, (*path, str(key)))
                if key in result
                else copy.deepcopy(value)
            )
        return result
    if isinstance(base, list) and isinstance(addition, list):
        # Source tiers are ordered matchers: a distribution's more-specific
        # match must run before the commercial generic fallback while both
        # definitions remain in the effective snapshot.
        result = copy.deepcopy(addition if path == ("source_tiers",) else base)
        candidates = base if path == ("source_tiers",) else addition
        seen = {_stable_json(item) for item in result}
        for item in candidates:
            marker = _stable_json(item)
            if marker not in seen:
                result.append(copy.deepcopy(item))
                seen.add(marker)
        return result
    if isinstance(base, bool) and isinstance(addition, bool):
        return base or addition
    return copy.deepcopy(addition)


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@lru_cache(maxsize=32)
def _load_citation_policy_document_cached(
    path: str,
    expected_policy_id: str,
    expected_layer: CitationPolicyLayer,
    revision_prefix: str,
) -> dict[str, Any]:
    policy_path = Path(path)
    if policy_path.stat().st_size > _MAX_POLICY_BYTES:
        raise RuntimeError("citation policy file exceeds 128 KiB")
    payload = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("citation policy must be a mapping")
    if payload.get("policy_id") != expected_policy_id:
        raise RuntimeError("citation policy id mismatch")
    if payload.get("layer") != expected_layer:
        raise RuntimeError("citation policy layer mismatch")
    revision = payload.get("version")
    if not isinstance(revision, str) or not revision.startswith(revision_prefix):
        raise RuntimeError("citation policy version is invalid")
    activation = payload.get("activation")
    if not isinstance(activation, dict) or activation.get("default_mode") not in _MODE_STRENGTH:
        raise RuntimeError("citation policy activation is invalid")
    for key in ("rules", "failure"):
        if key in payload and not isinstance(payload[key], dict):
            raise RuntimeError(f"citation policy {key} must be a mapping")
    _validate_task_coverage_policy(payload.get("task_coverage"))
    # Validate JSON safety and the host/kernel metadata budget up front.
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > _MAX_POLICY_BYTES:
        raise RuntimeError("citation policy payload exceeds 128 KiB")
    return payload


def _validate_task_coverage_policy(value: Any) -> None:
    """Validate passive Task Coverage guidance.

    The policy is deliberately incapable of expressing requirements,
    retrieval plans, tool budgets or Host remediation. It only supplies
    static review vocabulary to the Runtime's one native continuation and to
    offline evaluation fixtures.
    """

    if value is None:
        return
    if not isinstance(value, dict):
        raise RuntimeError("citation policy task_coverage must be a mapping")
    unknown_sections = set(value) - set(_TASK_COVERAGE_SECTIONS)
    if unknown_sections:
        raise RuntimeError(
            "citation policy task_coverage has unknown sections: "
            + ", ".join(sorted(unknown_sections))
        )
    revision = value.get("revision")
    if not isinstance(revision, str) or not revision:
        raise RuntimeError("citation policy task_coverage revision is invalid")

    guidance = value.get("review_guidance")
    if not isinstance(guidance, dict) or set(guidance) != _TASK_COVERAGE_GUIDANCE_KEYS:
        raise RuntimeError(
            "citation policy task_coverage review_guidance requires material_gap_types, "
            "completion_dimensions, source_boundary_notes and supplement_rules"
        )
    for key in ("material_gap_types", "completion_dimensions", "source_boundary_notes"):
        _validate_unique_nonempty_strings(guidance.get(key), f"review_guidance.{key}")

    supplement_rules = guidance.get("supplement_rules")
    if (
        not isinstance(supplement_rules, dict)
        or set(supplement_rules) != _TASK_COVERAGE_SUPPLEMENT_RULES
    ):
        raise RuntimeError(
            "citation policy task_coverage supplement_rules requires append_only, "
            "do_not_repeat_completed_content and preserve_visible_history"
        )
    for key in sorted(_TASK_COVERAGE_SUPPLEMENT_RULES):
        if supplement_rules.get(key) is not True:
            raise RuntimeError(
                f"citation policy task_coverage supplement_rules.{key} must be true"
            )

    evaluation = value.get("evaluation")
    if not isinstance(evaluation, dict) or set(evaluation) != {"scenario_families"}:
        raise RuntimeError(
            "citation policy task_coverage evaluation requires scenario_families"
        )
    _validate_unique_nonempty_strings(
        evaluation.get("scenario_families"),
        "evaluation.scenario_families",
    )


def _validate_unique_nonempty_strings(value: Any, path: str) -> None:
    if not isinstance(value, list) or not all(
        isinstance(entry, str) and entry for entry in value
    ):
        raise RuntimeError(
            f"citation policy task_coverage {path} must contain non-empty strings"
        )
    if len(value) != len(set(value)):
        raise RuntimeError(f"citation policy task_coverage {path} has duplicate entries")


def load_citation_policy_document(
    path: Path,
    *,
    expected_policy_id: str,
    expected_layer: CitationPolicyLayer,
    revision_prefix: str,
) -> dict[str, Any]:
    return copy.deepcopy(
        _load_citation_policy_document_cached(
            str(path.resolve()),
            expected_policy_id,
            expected_layer,
            revision_prefix,
        )
    )


__all__ = [
    "CitationPolicyLayer",
    "CitationPolicyMode",
    "CitationQualityPolicyPort",
    "CitationQualityPolicyRegistry",
    "CitationQualityPolicySnapshot",
    "NoopCitationQualityPolicy",
    "PackagedCitationQualityPolicy",
    "citation_policy_activation_mode",
    "citation_policy_snapshot_from_document",
    "load_citation_policy_document",
    "merge_citation_quality_policy_snapshots",
]
