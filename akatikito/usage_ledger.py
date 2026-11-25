"""Local usage ledger. Prices are explicit owner estimates, not invented rates."""
from __future__ import annotations
import calendar
import json,math,os
from .process_lock import FileLock
from datetime import date
from pathlib import Path

_SPARK_BARS = "▁▂▃▄▅▆▇█"


def _valid_prices(prices) -> bool:
    return bool(
        prices
        and all(
            k in prices and isinstance(prices[k], (int, float)) and math.isfinite(prices[k]) and prices[k] >= 0
            for k in ("input", "cached", "output")
        )
    )


def _add_bucket(row: dict, values: dict, cost: float | None, saved: float = 0.0) -> None:
    for k, v in values.items():
        row[k] = row.get(k, 0) + v
    row["calls"] = row.get("calls", 0) + 1
    row["cost"] = row.get("cost", 0.0) + (cost or 0.0)
    row["unknown"] = row.get("unknown", 0) + int(cost is None)
    if saved > 0:
        row["cache_saved"] = row.get("cache_saved", 0.0) + saved


def _sparkline(nums: list[float]) -> str:
    if not nums:
        return ""
    hi = max(nums)
    if hi <= 0:
        return _SPARK_BARS[0] * len(nums)
    scale = len(_SPARK_BARS) - 1
    return "".join(_SPARK_BARS[min(scale, int(round(v / hi * scale)))] for v in nums)


def _rank_breakdown(mapping: dict) -> list[dict]:
    rows = []
    for name, row in (mapping or {}).items():
        if not isinstance(row, dict):
            continue
        tokens = int(row.get("prompt_tokens", 0)) + int(row.get("completion_tokens", 0))
        rows.append({
            "name": str(name),
            "cost": round(float(row.get("cost", 0.0)), 4),
            "tokens": tokens,
            "calls": int(row.get("calls", 0)),
            "unknown": int(row.get("unknown", 0)),
        })
    rows.sort(key=lambda r: (r["cost"], r["tokens"]), reverse=True)
    return rows[:10]


class UsageLedger:
    def __init__(self,path):self.path=Path(path);self.lock=FileLock(self.path.with_suffix('.lock'))
    def load(self):
        if not self.path.exists():return {'days':{},'sessions':{}}
        value=json.loads(self.path.read_text(encoding='utf-8'))
        if not isinstance(value,dict) or not isinstance(value.get('days'),dict) or not isinstance(value.get('sessions'),dict):raise ValueError('Μη έγκυρο αρχείο κατανάλωσης.')
        return value
    def view(self,session,day=None):
        with self.lock:
            data=self.load();day=day or date.today().isoformat()
            return {'day':data['days'].get(day,{}),'session':data['sessions'].get(session,{})}
    def check(self,budget,session,prices=None):
        if not budget:return ''
        view=self.view(session)['day']
        if not prices or any(k not in prices for k in ('input','cached','output')) or view.get('unknown',0):return 'Το ημερήσιο όριο κόστους χρειάζεται τιμές μοντέλου και πλήρως αποτιμημένη σημερινή χρήση.'
        if view.get('cost',0)>=budget:return 'Έφτασες το ημερήσιο όριο κόστους. Δεν έγινε νέα κλήση μοντέλου.'
        return ''
    def record(self, session, usage, prices=None, day=None, *, model: str = "", project: str = ""):
        values = {k: max(0, int(usage.get(k, 0) or 0)) for k in ("prompt_tokens", "completion_tokens", "cache_hit_tokens")}
        hit = min(values["prompt_tokens"], values["cache_hit_tokens"])
        cost = None
        saved = 0.0
        if _valid_prices(prices):
            cost = ((values["prompt_tokens"] - hit) * prices["input"] + hit * prices["cached"] + values["completion_tokens"] * prices["output"]) / 1_000_000
            saved = max(0.0, hit * (prices["input"] - prices["cached"]) / 1_000_000)
        with self.lock:
            data = self.load()
            day_str = day or date.today().isoformat()
            month_str = day_str[:7]
            for bucket, key in (("days", day_str), ("sessions", session)):
                row = data[bucket].setdefault(key, {"prompt_tokens": 0, "completion_tokens": 0, "cache_hit_tokens": 0, "calls": 0, "cost": 0.0, "unknown": 0})
                _add_bucket(row, values, cost, saved)
            months = data.setdefault("months", {})
            m_row = months.setdefault(month_str, {"by_model": {}, "by_project": {}, "cache_saved": 0.0})
            if saved > 0:
                m_row["cache_saved"] = float(m_row.get("cache_saved", 0.0)) + saved
            if model:
                _add_bucket(m_row.setdefault("by_model", {}).setdefault(str(model), {}), values, cost)
            if project:
                _add_bucket(m_row.setdefault("by_project", {}).setdefault(str(project), {}), values, cost)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(".tmp")
            temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temp, self.path)
        return cost

    def monthly_insights(self, month: str | None = None, *, today: date | None = None) -> dict:
        """Aggregate monthly tokens, owner-priced cost, sparkline, cache savings, and top models/projects."""
        ref = today or date.today()
        target = (month or ref.strftime("%Y-%m")).strip()[:7]
        with self.lock:
            data = self.load()
        days_map = data.get("days") or {}
        month_meta = (data.get("months") or {}).get(target) or {}
        try:
            year_i, mon_i = int(target[:4]), int(target[5:7])
            days_in_month = calendar.monthrange(year_i, mon_i)[1]
        except (ValueError, IndexError):
            year_i, mon_i, days_in_month = ref.year, ref.month, 30

        daily = []
        tot_prompt = tot_comp = tot_hit = tot_calls = tot_unknown = 0
        tot_cost = 0.0
        for d in range(1, days_in_month + 1):
            d_key = f"{year_i:04d}-{mon_i:02d}-{d:02d}"
            row = days_map.get(d_key) or {}
            p = int(row.get("prompt_tokens", 0))
            c = int(row.get("completion_tokens", 0))
            h = int(row.get("cache_hit_tokens", 0))
            calls = int(row.get("calls", 0))
            cost = float(row.get("cost", 0.0))
            unk = int(row.get("unknown", 0))
            tot_prompt += p; tot_comp += c; tot_hit += h; tot_calls += calls; tot_cost += cost; tot_unknown += unk
            daily.append({"day": d_key, "cost": round(cost, 4), "tokens": p + c, "calls": calls})

        has_prices = tot_calls > tot_unknown
        cache_pct = round(100 * min(tot_prompt, tot_hit) / tot_prompt) if tot_prompt > 0 else 0
        cache_saved = round(float(month_meta.get("cache_saved", 0.0)), 4)
        elapsed = ref.day if target == ref.strftime("%Y-%m") else days_in_month
        projected = round((tot_cost / max(1, elapsed)) * days_in_month, 4) if has_prices and tot_cost > 0 else None
        spark_vals = [row["cost"] if has_prices else float(row["tokens"]) for row in daily[:elapsed]]
        return {
            "month": target,
            "total_cost": round(tot_cost, 4),
            "projected_month_cost": projected,
            "has_prices": has_prices,
            "unknown_calls": tot_unknown,
            "calls": tot_calls,
            "prompt_tokens": tot_prompt,
            "completion_tokens": tot_comp,
            "cache_hit_tokens": tot_hit,
            "cache_hit_pct": cache_pct,
            "cache_saved": cache_saved,
            "sparkline": _sparkline(spark_vals),
            "daily": daily[:elapsed],
            "by_model": _rank_breakdown(month_meta.get("by_model") or {}),
            "by_project": _rank_breakdown(month_meta.get("by_project") or {}),
        }


def summarize_turn_delta(before: dict, after: dict, prices: dict | None = None) -> dict:
    """Compute per-answer token delta, cache-hit percentage, and optional EUR cost."""
    prompt = max(0, int(after.get('prompt_tokens', 0) or 0) - int(before.get('prompt_tokens', 0) or 0))
    comp = max(0, int(after.get('completion_tokens', 0) or 0) - int(before.get('completion_tokens', 0) or 0))
    hit = min(prompt, max(0, int(after.get('cache_hit_tokens', 0) or 0) - int(before.get('cache_hit_tokens', 0) or 0)))
    pct = round(100 * hit / prompt) if prompt > 0 and hit > 0 else 0
    cost = None
    if prices and all(k in prices and isinstance(prices[k], (int, float)) and math.isfinite(prices[k]) and prices[k] >= 0 for k in ('input', 'cached', 'output')):
        cost = round(((prompt - hit) * prices['input'] + hit * prices['cached'] + comp * prices['output']) / 1_000_000, 5)
    return {'prompt_tokens': prompt, 'completion_tokens': comp, 'cache_hit_tokens': hit, 'cache_hit_pct': pct, 'cost_eur': cost}
