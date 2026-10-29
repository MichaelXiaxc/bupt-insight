"""Source registry. Adding a source = one line here; the rest of the code is unchanged."""

from sources import byr

REGISTRY = {"byr": byr}


def get(name):
    if name not in REGISTRY:
        raise SystemExit(f"未知的信息源：{name}。可选：{', '.join(REGISTRY)}")
    return REGISTRY[name]
