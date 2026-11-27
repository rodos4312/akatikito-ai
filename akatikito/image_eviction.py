"""Cache-friendly batch eviction of old image_url blocks on outbound LLM request views.

Never mutates raw local conversation history: operates exclusively on the outbound view list.
Retiring images in batches ONLY when crossing a real count or byte threshold preserves exact
prompt-cache prefixes on ordinary vision turns instead of invalidating cache on every turn.
"""
from __future__ import annotations

import copy
from typing import Any

DEFAULT_IMAGE_LIMIT = 12
DEFAULT_IMAGE_BUDGET_BYTES = 15 * 1024 * 1024  # 15 MiB
DEFAULT_EVICT_BATCH = 6
DEFAULT_KEEP_RECENT = 2
EVICTED_IMAGE_PLACEHOLDER = "[Παλαιότερη εικόνα αφαιρέθηκε από το ενεργό context για εξοικονόμηση χώρου]"


def _image_block_size(block: Any) -> int:
    if not isinstance(block, dict) or block.get("type") != "image_url":
        return 0
    img = block.get("image_url")
    url = img.get("url", "") if isinstance(img, dict) else str(img or "")
    return len(str(url).encode("utf-8", errors="ignore"))


def _locate_image_slots(view: list[dict]) -> list[tuple[int, int, int]]:
    """Return [(msg_idx, block_idx, byte_size), ...] in chronological order."""
    slots: list[tuple[int, int, int]] = []
    for mi, msg in enumerate(view):
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for bi, block in enumerate(content):
            if isinstance(block, dict) and block.get("type") == "image_url":
                slots.append((mi, bi, _image_block_size(block)))
    return slots


def _select_evict_count(
    slots: list[tuple[int, int, int]],
    *,
    max_images: int,
    max_bytes: int,
    batch_size: int,
    keep_recent: int,
) -> int:
    total_count = len(slots)
    total_bytes = sum(sz for _, _, sz in slots)
    if total_count <= max_images and total_bytes <= max_bytes:
        return 0
    max_evictable = max(0, total_count - max(0, keep_recent))
    if max_evictable == 0:
        return 0
    count = min(max_evictable, max(1, batch_size))
    rem_count = total_count - count
    rem_bytes = total_bytes - sum(sz for _, _, sz in slots[:count])
    while count < max_evictable and (rem_count > max_images or rem_bytes > max_bytes):
        rem_bytes -= slots[count][2]
        rem_count -= 1
        count += 1
    return count


def evict_old_outbound_images(
    view: list[dict],
    *,
    max_images: int = DEFAULT_IMAGE_LIMIT,
    max_bytes: int = DEFAULT_IMAGE_BUDGET_BYTES,
    batch_size: int = DEFAULT_EVICT_BATCH,
    keep_recent: int = DEFAULT_KEEP_RECENT,
) -> tuple[list[dict], int]:
    """Batch-evict oldest image_url blocks when limits are exceeded; returns (new_view, evicted_count)."""
    slots = _locate_image_slots(view)
    if not slots:
        return view, 0
    evict_n = _select_evict_count(
        slots,
        max_images=max(1, int(max_images)),
        max_bytes=max(1024, int(max_bytes)),
        batch_size=max(1, int(batch_size)),
        keep_recent=max(0, int(keep_recent)),
    )
    if evict_n <= 0:
        return view, 0
    to_evict = {(mi, bi) for mi, bi, _ in slots[:evict_n]}
    touched_msgs = {mi for mi, _ in to_evict}
    out = list(view)
    for mi in touched_msgs:
        msg_copy = copy.deepcopy(out[mi])
        blocks = msg_copy.get("content")
        if isinstance(blocks, list):
            for bi in range(len(blocks)):
                if (mi, bi) in to_evict:
                    blocks[bi] = {"type": "text", "text": EVICTED_IMAGE_PLACEHOLDER}
        out[mi] = msg_copy
    return out, evict_n
