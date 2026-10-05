"""信息源注册表。加新源 = 在这里加一行，其余代码不用动。"""
from sources import byr, portal

REGISTRY = {
    "byr": byr,
    "portal": portal,
}


def get(name):
    if name not in REGISTRY:
        raise SystemExit(f"未知的信息源：{name}。可选：{', '.join(REGISTRY)}")
    return REGISTRY[name]
