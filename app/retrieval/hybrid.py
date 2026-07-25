from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RankedItem:
    chunk_id: str
    scores: dict[str, float] = field(default_factory=dict)
    payload: dict = field(default_factory=dict)


def reciprocal_rank_fusion(
    ranked_lists: dict[str, list[str]],
    k: int = 60,
) -> list[tuple[str, float, dict[str, float]]]:
    """
    RRF: score(d) = sum_i 1 / (k + rank_i(d))
    ranked_lists: channel -> ordered chunk_ids (best first).
    Returns (chunk_id, rrf_score, per_channel_rrf_parts).
    """
    scores: dict[str, float] = {}
    parts: dict[str, dict[str, float]] = {}
    for channel, ordered in ranked_lists.items():
        for rank, chunk_id in enumerate(ordered, start=1):
            contrib = 1.0 / (k + rank)
            scores[chunk_id] = scores.get(chunk_id, 0.0) + contrib
            parts.setdefault(chunk_id, {})[f"rrf_{channel}"] = contrib
    fused = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [(cid, score, parts.get(cid, {})) for cid, score in fused]


def weighted_fusion(
    channel_scores: dict[str, dict[str, float]],
    weights: dict[str, float],
) -> list[tuple[str, float, dict[str, float]]]:
    """
    Min-max normalize each channel then weighted sum.
    channel_scores: channel -> {chunk_id: raw_score}
    """
    normalized: dict[str, dict[str, float]] = {}
    for channel, mapping in channel_scores.items():
        if not mapping:
            normalized[channel] = {}
            continue
        vals = list(mapping.values())
        lo, hi = min(vals), max(vals)
        if hi - lo < 1e-12:
            normalized[channel] = {cid: 1.0 for cid in mapping}
        else:
            normalized[channel] = {
                cid: (score - lo) / (hi - lo) for cid, score in mapping.items()
            }

    all_ids = set()
    for mapping in normalized.values():
        all_ids.update(mapping.keys())

    fused_scores: dict[str, float] = {}
    detail: dict[str, dict[str, float]] = {}
    for cid in all_ids:
        total = 0.0
        detail[cid] = {}
        for channel, w in weights.items():
            n = normalized.get(channel, {}).get(cid, 0.0)
            detail[cid][f"norm_{channel}"] = n
            total += w * n
        fused_scores[cid] = total

    fused = sorted(fused_scores.items(), key=lambda x: x[1], reverse=True)
    return [(cid, score, detail.get(cid, {})) for cid, score in fused]
