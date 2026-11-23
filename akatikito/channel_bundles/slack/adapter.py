"""Slack Web API connector (Bot Token xoxb-...). No model invocation inside connector."""
from __future__ import annotations

import json
import time
from urllib.parse import urlencode

from akatikito.channel_sdk import ChannelError, HTTP, Inbound, chunks


def _parse_cursors(raw: str, channels: list[str], default_ts: float) -> dict[str, float]:
    out = {ch: default_ts for ch in channels}
    s = str(raw or "").strip()
    if not s:
        return out
    if s.startswith("{"):
        try:
            loaded = json.loads(s)
            if isinstance(loaded, dict):
                for ch in channels:
                    val = loaded.get(ch)
                    if val is not None:
                        out[ch] = float(val)
                return out
        except (ValueError, TypeError):
            pass
    elif s.replace(".", "", 1).isdigit():
        val = float(s)
        return {ch: val for ch in channels}
    return out


class SlackChannel:
    def __init__(self, config: dict, http=None):
        token = str(config.get("token") or "").strip()
        if not token:
            raise ChannelError("Χρειάζεται Slack Bot Token (xoxb-...).")
        self.http = http or HTTP("https://slack.com/api", headers={"Authorization": f"Bearer {token}"})
        self.channels = [str(c).strip() for c in (config.get("allowed_chats") or []) if str(c).strip()]
        self.bot_id = ""

    def connect(self) -> str:
        res = self.http.request("POST", "/auth.test", {})
        if not res.get("ok"):
            raise ChannelError(f"Αποτυχία σύνδεσης Slack: {res.get('error') or 'auth.test failed'}")
        self.bot_id = str(res.get("user_id") or res.get("bot_id") or "")
        return self.bot_id

    def poll(self, cursor: str, stopped) -> tuple[str, list[Inbound]]:
        if not self.channels:
            time.sleep(5)
            return cursor or "{}", []
        cursors = _parse_cursors(cursor, self.channels, time.time())
        events: list[Inbound] = []
        for ch_id in self.channels:
            if stopped():
                break
            ch_last = cursors[ch_id]
            ch_next = ch_last
            all_msgs: list[dict] = []
            page_cursor = ""
            # Paginate up to 5 pages per channel so bursts never drop messages
            for _ in range(5):
                if stopped():
                    break
                params = {"channel": ch_id, "oldest": f"{ch_last:.6f}", "limit": 50}
                if page_cursor:
                    params["cursor"] = page_cursor
                res = self.http.request("GET", f"/conversations.history?{urlencode(params)}")
                if not res.get("ok"):
                    break
                batch = res.get("messages") or []
                all_msgs.extend(batch)
                page_cursor = str((res.get("response_metadata") or {}).get("next_cursor") or "").strip()
                if not page_cursor or not res.get("has_more"):
                    break
            # Process oldest-first strictly per channel
            valid: list[tuple[float, dict]] = []
            for m in all_msgs:
                ts_str = str(m.get("ts") or "")
                try:
                    ts_val = float(ts_str)
                except ValueError:
                    continue
                if ts_val > ch_last:
                    valid.append((ts_val, m))
            valid.sort(key=lambda x: x[0])
            for ts_val, m in valid:
                ch_next = max(ch_next, ts_val)
                if m.get("bot_id") or m.get("subtype"):
                    continue
                user = str(m.get("user") or "")
                if not user or user == self.bot_id:
                    continue
                text = str(m.get("text") or "").strip()
                if not text:
                    continue
                ts_str = str(m.get("ts") or "")
                thread_ts = str(m.get("thread_ts") or ts_str)
                events.append(Inbound("slack", self.bot_id, ch_id, user, ts_str, text, thread_ts, True))
            cursors[ch_id] = ch_next
        if not events:
            time.sleep(3)
        return json.dumps({k: f"{v:.6f}" for k, v in cursors.items()}, sort_keys=True), events

    def send(self, event: Inbound, text: str) -> None:
        for part in chunks(text, 3500):
            payload = {"channel": event.chat, "text": part}
            if event.thread:
                payload["thread_ts"] = event.thread
            res = self.http.request("POST", "/chat.postMessage", payload)
            if not res.get("ok"):
                raise ChannelError(f"Αποτυχία αποστολής Slack: {res.get('error') or 'chat.postMessage failed'}")

    def close(self) -> None:
        self.http.close()


def create(config: dict) -> SlackChannel:
    return SlackChannel(config)
