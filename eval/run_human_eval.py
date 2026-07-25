"""
Human evaluation helper for analyze-stage outputs.

Randomly samples N analyze results, presents each finding to the annotator,
records:
  - citation_accurate  (1/0): cited path:lines exist and are relevant to the claim
  - conclusion_grounded (1/0): the claim is supported by the cited evidence

Then aggregates citation accuracy and grounding accuracy.

Input sources (pick one):
  1. JSONL dump of analyze runs  (--input eval/datasets/analyze_samples.jsonl)
  2. Directory of report_json / AgentRunRecord JSON files (--dir ...)
  3. Recent Postgres agent_runs   (--from-audit) when REPOSCOPE_DATABASE_URL is set

Examples:
  python -m eval.run_human_eval --input eval/datasets/analyze_samples.jsonl -n 5
  python -m eval.run_human_eval --from-audit -n 20 --seed 42
"""

from __future__ import annotations

import argparse
import json
import os
import random
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "eval" / "datasets" / "analyze_samples.jsonl"
DEFAULT_OUT = ROOT / "eval" / "reports" / "human_labels.jsonl"
REPORT_MD = ROOT / "eval" / "reports" / "human_eval_summary.md"


@dataclass
class AnalyzeSample:
    sample_id: str
    repo_id: str
    question: str
    intent: str | None
    findings: list[dict[str, Any]]
    report_markdown: str = ""
    source: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass
class Label:
    sample_id: str
    finding_idx: int
    claim: str
    citations: list[str]
    citation_accurate: int  # 1 / 0
    conclusion_grounded: int  # 1 / 0
    notes: str = ""
    labeled_at: str = ""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_findings(obj: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize findings from WorkflowResult / MCP payload / AgentRunRecord shapes."""
    if isinstance(obj.get("findings"), list) and obj["findings"]:
        return list(obj["findings"])

    result = obj.get("result")
    if isinstance(result, dict):
        if isinstance(result.get("findings"), list) and result["findings"]:
            return list(result["findings"])
        report = result.get("report_json")
        if isinstance(report, dict) and isinstance(report.get("findings"), list):
            return list(report["findings"])

    report = obj.get("report_json")
    if isinstance(report, dict) and isinstance(report.get("findings"), list):
        return list(report["findings"])

    return []


def _normalize_sample(obj: dict[str, Any], fallback_id: str, source: str) -> AnalyzeSample | None:
    findings = _extract_findings(obj)
    if not findings:
        return None

    sample_id = str(
        obj.get("sample_id")
        or obj.get("run_id")
        or obj.get("id")
        or fallback_id
    )
    question = str(
        obj.get("question")
        or (obj.get("result") or {}).get("question")
        or ""
    )
    repo_id = str(
        obj.get("repo_id")
        or (obj.get("result") or {}).get("repo_id")
        or "unknown"
    )
    intent = obj.get("intent") or (obj.get("result") or {}).get("intent")
    md = str(
        obj.get("report_markdown")
        or (obj.get("result") or {}).get("report_markdown")
        or ""
    )
    return AnalyzeSample(
        sample_id=sample_id,
        repo_id=repo_id,
        question=question,
        intent=str(intent) if intent else None,
        findings=findings,
        report_markdown=md,
        source=source,
        raw=obj,
    )


def load_jsonl(path: Path) -> list[AnalyzeSample]:
    samples: list[AnalyzeSample] = []
    if not path.exists():
        return samples
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        obj = json.loads(line)
        sample = _normalize_sample(obj, fallback_id=f"{path.stem}-{i:03d}", source=str(path))
        if sample:
            samples.append(sample)
    return samples


def load_dir(path: Path) -> list[AnalyzeSample]:
    samples: list[AnalyzeSample] = []
    for fp in sorted(path.glob("**/*.json")):
        obj = json.loads(fp.read_text(encoding="utf-8"))
        sample = _normalize_sample(obj, fallback_id=fp.stem, source=str(fp))
        if sample:
            samples.append(sample)
    return samples


def load_from_audit(limit: int = 100) -> list[AnalyzeSample]:
    from app.audit import create_agent_run_store

    database_url = os.environ.get("REPOSCOPE_DATABASE_URL")
    store = create_agent_run_store(database_url)
    if store.backend != "postgres":
        print(
            "WARNING: audit store is in_memory — no persisted runs available.\n"
            "Export analyze dumps to JSONL, or set REPOSCOPE_DATABASE_URL."
        )
        return []
    records = store.list_recent(limit=limit)
    samples: list[AnalyzeSample] = []
    for rec in records:
        obj = rec.model_dump()
        sample = _normalize_sample(obj, fallback_id=rec.run_id, source="audit")
        if sample:
            samples.append(sample)
    return samples


def _ask_binary(prompt: str) -> int:
    while True:
        ans = input(f"{prompt} [y/n]: ").strip().lower()
        if ans in {"y", "yes", "1"}:
            return 1
        if ans in {"n", "no", "0"}:
            return 0
        print("  Please answer y or n.")


def _display_finding(sample: AnalyzeSample, idx: int, finding: dict[str, Any]) -> None:
    claim = finding.get("claim") or finding.get("description") or "(no claim)"
    citations = finding.get("citations") or []
    # MCP FindingOut may nest citations under evidence
    if not citations and isinstance(finding.get("evidence"), list):
        citations = [
            e.get("citation") if isinstance(e.get("citation"), str) else e.get("citation", {}).get("format", "")
            for e in finding["evidence"]
            if isinstance(e, dict)
        ]
        citations = [c for c in citations if c]

    print()
    print("=" * 72)
    print(f"Sample : {sample.sample_id}  (repo={sample.repo_id}, intent={sample.intent})")
    print(f"Q      : {sample.question}")
    print(f"Finding #{idx + 1}/{len(sample.findings)}")
    print("-" * 72)
    print(f"Claim  : {claim}")
    print("Citations:")
    if citations:
        for c in citations:
            print(f"  - {c}")
    else:
        print("  (none)")
    conf = finding.get("confidence")
    tier = finding.get("evidence_tier")
    if conf or tier:
        print(f"Meta   : confidence={conf}  evidence_tier={tier}")
    print("-" * 72)


def annotate_samples(samples: list[AnalyzeSample]) -> list[Label]:
    labels: list[Label] = []
    total_findings = sum(len(s.findings) for s in samples)
    done = 0

    for sample in samples:
        for idx, finding in enumerate(sample.findings):
            done += 1
            print(f"\nProgress: finding {done}/{total_findings}")
            _display_finding(sample, idx, finding)
            cite_ok = _ask_binary("Is citation accurate (exists + relevant)?")
            grounded = _ask_binary("Is the conclusion grounded in the citations?")
            notes = input("Optional notes (Enter to skip): ").strip()

            citations = finding.get("citations") or []
            labels.append(
                Label(
                    sample_id=sample.sample_id,
                    finding_idx=idx,
                    claim=str(finding.get("claim") or ""),
                    citations=list(citations) if isinstance(citations, list) else [],
                    citation_accurate=cite_ok,
                    conclusion_grounded=grounded,
                    notes=notes,
                    labeled_at=_utc_now(),
                )
            )
    return labels


def summarize(labels: list[Label]) -> dict[str, Any]:
    n = len(labels)
    if n == 0:
        return {
            "n_findings": 0,
            "citation_accuracy": 0.0,
            "grounding_accuracy": 0.0,
            "both_ok_rate": 0.0,
        }
    cite = sum(l.citation_accurate for l in labels) / n
    ground = sum(l.conclusion_grounded for l in labels) / n
    both = sum(
        1 for l in labels if l.citation_accurate and l.conclusion_grounded
    ) / n
    return {
        "n_findings": n,
        "n_samples": len({l.sample_id for l in labels}),
        "citation_accuracy": cite,
        "grounding_accuracy": ground,
        "both_ok_rate": both,
    }


def append_labels(path: Path, labels: list[Label]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for lab in labels:
            fh.write(json.dumps(asdict(lab), ensure_ascii=False) + "\n")


def write_summary_md(stats: dict[str, Any], labels_path: Path) -> Path:
    REPORT_MD.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    md = f"""# Human Evaluation Summary

- Generated: {now}
- Labels file: `{labels_path.as_posix()}`
- Findings labeled: **{stats.get('n_findings', 0)}**
- Unique analyze samples: **{stats.get('n_samples', 0)}**

## Accuracy

| Metric | Value |
|---|---:|
| Citation accuracy | {stats.get('citation_accuracy', 0) * 100:.1f}% |
| Conclusion grounding accuracy | {stats.get('grounding_accuracy', 0) * 100:.1f}% |
| Both OK rate | {stats.get('both_ok_rate', 0) * 100:.1f}% |

## Rubric (for consistency)

- **Citation accurate**: the cited `path:start-end` exists in the repo *and* is
  topically related to the claim (not a random file that happened to be retrieved).
- **Conclusion grounded**: a careful reader of the cited spans would accept the
  claim; no unsupported leaps.

Copy these numbers into `eval/reports/evaluation_report_template.md`.
"""
    REPORT_MD.write_text(md, encoding="utf-8")
    return REPORT_MD


def main() -> None:
    parser = argparse.ArgumentParser(description="Human eval helper for analyze outputs")
    src = parser.add_mutually_exclusive_group()
    src.add_argument("--input", type=Path, default=None, help="JSONL of analyze dumps")
    src.add_argument("--dir", type=Path, default=None, help="Directory of JSON dumps")
    src.add_argument(
        "--from-audit",
        action="store_true",
        help="Load recent agent_runs from Postgres audit store",
    )
    parser.add_argument("-n", type=int, default=10, help="Number of analyze samples to label")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List sampled items without prompting for labels",
    )
    args = parser.parse_args()

    if args.from_audit:
        pool = load_from_audit(limit=max(args.n * 5, 50))
    elif args.dir:
        pool = load_dir(args.dir)
    else:
        path = args.input or DEFAULT_INPUT
        pool = load_jsonl(path)

    if not pool:
        print("No analyze samples found.")
        print("Provide --input JSONL, --dir of JSON dumps, or --from-audit with Postgres.")
        print(f"A starter file lives at: {DEFAULT_INPUT}")
        return

    rng = random.Random(args.seed)
    n = min(args.n, len(pool))
    samples = rng.sample(pool, n)

    print(f"Loaded {len(pool)} samples; labeling {n} (seed={args.seed})")
    if args.dry_run:
        for s in samples:
            print(f"  - {s.sample_id}: {s.question[:80]!r}  findings={len(s.findings)}")
        return

    labels = annotate_samples(samples)
    append_labels(args.out, labels)
    stats = summarize(labels)
    md_path = write_summary_md(stats, args.out)

    print()
    print("=" * 72)
    print("Human eval summary")
    print(f"  findings labeled     : {stats['n_findings']}")
    print(f"  citation accuracy    : {stats['citation_accuracy'] * 100:.1f}%")
    print(f"  grounding accuracy   : {stats['grounding_accuracy'] * 100:.1f}%")
    print(f"  both OK rate         : {stats['both_ok_rate'] * 100:.1f}%")
    print(f"  labels appended to   : {args.out}")
    print(f"  summary written to   : {md_path}")
    print("=" * 72)


if __name__ == "__main__":
    main()
