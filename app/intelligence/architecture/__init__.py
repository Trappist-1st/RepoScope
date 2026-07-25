"""Architecture Intelligence package (Iteration 3)."""

from app.intelligence.architecture.analyzer import (
    ArchitectureAnalyzer,
    analyze_architecture,
    analyze_architecture_markdown,
)
from app.intelligence.architecture.format import format_architecture_markdown
from app.intelligence.architecture.models import (
    ArchitectureFinding,
    ArchitectureFindingCategory,
    ArchitectureMetrics,
    ArchitectureModule,
    ArchitecturePatternKind,
    ArchitectureReport,
    ArchitectureReportMeta,
    EvidenceRef,
    EvidenceRefKind,
    FrameworkHit,
    InfraHit,
    InfraKind,
    ModuleMap,
    ModuleType,
    PatternMatch,
    RepositoryProfile,
)
from app.intelligence.architecture.modules import discover_modules
from app.intelligence.architecture.profile import build_repository_profile

__all__ = [
    "ArchitectureAnalyzer",
    "ArchitectureFinding",
    "ArchitectureFindingCategory",
    "ArchitectureMetrics",
    "ArchitectureModule",
    "ArchitecturePatternKind",
    "ArchitectureReport",
    "ArchitectureReportMeta",
    "EvidenceRef",
    "EvidenceRefKind",
    "FrameworkHit",
    "InfraHit",
    "InfraKind",
    "ModuleMap",
    "ModuleType",
    "PatternMatch",
    "RepositoryProfile",
    "analyze_architecture",
    "analyze_architecture_markdown",
    "build_repository_profile",
    "discover_modules",
    "format_architecture_markdown",
]
