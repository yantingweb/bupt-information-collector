"""关键词打分。词表在 rules.yaml，改表不用改代码。"""

from core.config import load_rules


class Scorer:
    def __init__(self, rules=None):
        r = rules or load_rules()
        self.groups = r["groups"]
        self.exclude = r.get("exclude") or {}
        self.grade_miss = r.get("grade_miss") or {}
        self.noise = [n for n in (r.get("noise") or []) if n]
        self.noise_cap = r["thresholds"]["noise_cap"]
        self.action_line = r["thresholds"]["action_line"]
        self.archive_line = r["thresholds"]["archive_line"]
        self.own_grad = str(r["profile"]["own_grad"])
        self.other_grad_penalty = r["profile"]["other_grad_penalty"]
        self.grad_own_key = f"{self.own_grad}届"

    def score(self, text: str) -> tuple:
        """返回 (总分, 命中理由列表)。"""
        low = (text or "").lower()
        total, hits = 0, []

        for gname, table in self.groups.items():
            for kw, w in table.items():
                if w and str(kw).lower() in low:
                    total += w
                    hits.append(f"{gname}:{kw}{'+' if w > 0 else ''}{w}")

        if self.grad_own_key in low:
            total += 8
            hits.append(f"本届{self.grad_own_key}+8")

        for kw, w in self.grade_miss.items():
            if str(kw) in low:
                total += w
                hits.append(f"{kw}{w}")

        for kw, w in self.exclude.items():
            if w and str(kw) in low:
                total += w
                hits.append(f"[排除]{kw}")

        for n in self.noise:
            if n in low:
                total += self.noise_cap
                hits.append(f"[噪声]{n}")

        return total, hits

    def grade(self, item_score: int) -> str:
        if item_score >= self.action_line:
            return "action"
        if item_score >= self.archive_line:
            return "fyi"
        return "drop"
