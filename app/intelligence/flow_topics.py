"""Topic lexicon + question normalization for Flow Entry Discovery."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class TopicSpec:
    topic: str
    terms: tuple[str, ...]


# Canonical topics → matching terms (EN + ZH + common aliases)
TOPIC_LEXICON: tuple[TopicSpec, ...] = (
    TopicSpec(
        "login",
        (
            "login",
            "signin",
            "sign_in",
            "sign-in",
            "authenticate",
            "authentication",
            "auth",
            "登录",
            "登陆",
            "鉴权",
            "认证",
        ),
    ),
    TopicSpec(
        "logout",
        ("logout", "signout", "sign_out", "登出", "注销"),
    ),
    TopicSpec(
        "register",
        ("register", "signup", "sign_up", "registration", "注册"),
    ),
    TopicSpec(
        "order_create",
        (
            "order",
            "create_order",
            "createorder",
            "place_order",
            "checkout",
            "订单",
            "下单",
            "创建订单",
        ),
    ),
    TopicSpec(
        "payment",
        ("payment", "pay", "checkout", "支付", "付款"),
    ),
    TopicSpec(
        "user",
        ("user", "profile", "account", "用户", "账号"),
    ),
    TopicSpec(
        "task_schedule",
        (
            "task_schedule",
            "任务调度",
            "任务提交",
            "调度流程",
            "schedule",
            "scheduler",
            "scheduling",
            "dispatch",
            "dispatcher",
            "submit",
            "task",
            "job",
            "workflow",
            "executor",
            "quartz",
            "cron",
            "调度",
            "任务",
            "作业",
            "工作流",
            "执行器",
            "提交",
        ),
    ),
)


_FLOW_INTENT = re.compile(
    r"(流程|怎么走|如何实现|调用链|call\s*chain|flow|trace|path|pipeline)",
    re.I,
)

_STOP_EN = frozenset(
    {
        "the",
        "what",
        "how",
        "flow",
        "path",
        "trace",
        "call",
        "chain",
        "please",
        "this",
        "that",
        "with",
        "from",
        "into",
    }
)

_STOP_ZH = frozenset(
    {
        "什么",
        "怎么",
        "怎样",
        "如何",
        "流程",
        "调用",
        "调用链",
        "一下",
        "这个",
        "那个",
        "相关",
    }
)


def is_flow_question(question: str) -> bool:
    q = question or ""
    if _FLOW_INTENT.search(q):
        return True
    # topic alone often implies flow when asking "what is X"
    topic, _ = extract_topic(q)
    return topic is not None and bool(
        re.search(r"(是什么|怎样|如何|what|how)", q, re.I)
    )


# When a topic hits, expand with aliases — NOT the entire lexicon (avoids
# "任务调度" also ranking every Workflow* symbol via unused term "workflow").
_TOPIC_EXPAND: dict[str, tuple[str, ...]] = {
    "login": ("login", "signin", "sign_in", "authenticate", "authentication", "auth"),
    "logout": ("logout", "signout", "sign_out"),
    "register": ("register", "signup", "sign_up", "registration"),
    "order_create": ("order", "create_order", "createorder", "place_order", "checkout"),
    "payment": ("payment", "pay", "checkout"),
    "user": ("user", "profile", "account"),
    "task_schedule": (
        "schedule",
        "scheduler",
        "scheduling",
        "dispatch",
        "dispatcher",
        "submit",
        "task",
        "job",
        "executor",
        "cron",
        "quartz",
    ),
}

# Extra expansions only when the question itself mentions these terms
_TOPIC_CONDITIONAL_EXPAND: dict[str, tuple[str, ...]] = {
    "工作流": ("workflow",),
    "workflow": ("workflow",),
    "executor": ("executor",),
    "执行器": ("executor",),
}


def extract_topic(question: str) -> tuple[str | None, list[str]]:
    """
    Return (topic_id, topic_terms).

    Prefers the lexicon topic whose terms hit the question; ties broken by
    longer term match then lexicon order.

    Terms = question hits + controlled aliases (not the full lexicon dump).
    """
    raw = (question or "").strip()
    q = raw.lower()
    if not q:
        return None, []

    best: TopicSpec | None = None
    best_hit_len = 0
    for spec in TOPIC_LEXICON:
        for term in spec.terms:
            t = term.lower()
            if t and t in q and len(t) > best_hit_len:
                best = spec
                best_hit_len = len(t)

    if best is not None:
        hits = [t for t in best.terms if t.lower() in q]
        terms = list(dict.fromkeys([*hits, *_TOPIC_EXPAND.get(best.topic, ())]))
        for key, extra in _TOPIC_CONDITIONAL_EXPAND.items():
            if key.lower() in q:
                terms.extend(extra)
        return best.topic, list(dict.fromkeys(terms))

    # Soft terms: ASCII identifiers + Chinese runs (len>=2)
    soft_en = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", raw)
    soft_en = [s.lower() for s in soft_en if s.lower() not in _STOP_EN]
    soft_zh = [
        z
        for z in re.findall(r"[\u4e00-\u9fff]{2,}", raw)
        if z not in _STOP_ZH
    ]
    soft = list(dict.fromkeys([*soft_zh, *soft_en]))[:12]
    return None, soft


def normalize_entry_hint(hint: str | None) -> str | None:
    if not hint:
        return None
    h = hint.strip().replace("\\", "/")
    if not h:
        return None
    # Allow "AuthController.login" without file — kept as substring needle
    return h
