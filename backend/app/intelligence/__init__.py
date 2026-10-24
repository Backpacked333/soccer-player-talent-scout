"""Evidence engines. No trained weights or automatic identity model are shipped.

Only lightweight contracts are imported here: optional engine dependencies never
run at import time. Numerical implementations live in action_value and ratings;
persistent reviewer decisions and pure longitudinal knowledge live in identity
and memory respectively.
"""
from app.intelligence.contracts import (
    CONTRACT_VERSION,
    ActionValueArtifact,
    ActionValueEngine,
    ActionValuePolicy,
    ActionValueResult,
    CohortScope,
    DuelRatingEngine,
    DuelRatingEstimate,
    DuelRatingPolicy,
    DuelRatingReport,
    EvaluationReport,
    EventEvidenceFields,
    EventSemantics,
    EvidenceAppearance,
    EvidenceMatch,
    EvidenceSnapshot,
    FitDiagnostics,
    IntelligenceContract,
    LongitudinalPoint,
    PlayerKnowledge,
    PlayerMemoryEngine,
    RelationshipEvidence,
    ReviewedAction,
    StreamDeclaration,
    TrackIdentityCorrection,
    TrackIdentityRecord,
    TrackIdentityWrite,
    canonical_fingerprint,
    chronological_match_split,
    utc_timestamp,
)

__all__ = [
    "CONTRACT_VERSION", "ActionValueArtifact", "ActionValueEngine",
    "ActionValuePolicy", "ActionValueResult", "CohortScope", "DuelRatingEngine",
    "DuelRatingEstimate", "DuelRatingPolicy", "DuelRatingReport",
    "EvaluationReport", "EventEvidenceFields", "EventSemantics", "EvidenceAppearance",
    "EvidenceMatch", "EvidenceSnapshot", "FitDiagnostics", "IntelligenceContract",
    "LongitudinalPoint", "PlayerKnowledge", "PlayerMemoryEngine",
    "RelationshipEvidence", "ReviewedAction", "StreamDeclaration",
    "TrackIdentityCorrection", "TrackIdentityRecord", "TrackIdentityWrite",
    "canonical_fingerprint", "chronological_match_split", "utc_timestamp",
]
