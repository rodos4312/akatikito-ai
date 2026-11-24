"""Cross-Provider Session-Sticky Smart Model Routing extracted from agent.py."""
from __future__ import annotations

from dataclasses import replace as _dc_replace

_COMPLEX_HINTS = (
    "debug", "refactor", "αρχιτεκτον", "έρευνα", "ερευνα", "research", "ανάλυσ", "αναλυσ",
    "σύγκριν", "συγκριν", "τιμολόγ", "τιμολογ", "excel", "security", "audit", "κώδικ", "κωδικ",
    "φτιάξε", "φτιαξε", "υλοποίη", "υλοποιη", "διόρθωσ", "διορθωσ", "script", "python",
)


def apply_model_routing(agent, effective: str, *, fresh: bool, is_direct_skill: bool) -> None:
    """Select fast/smart model once per fresh session when configured; never leak primary keys."""
    from . import agent as agent_mod

    fast_m = str(getattr(agent.config, "fast_model", "") or "").strip()
    smart_m = str(getattr(agent.config, "smart_model", "") or "").strip()
    has_images = any(
        isinstance(b, dict) and b.get("type") == "image_url"
        for b in (getattr(agent, "_attachment_blocks", None) or [])
    )
    if agent.config.auto_mode and (fast_m or smart_m) and not has_images:
        base_m = getattr(agent, "_default_model_name", agent.config.model)
        base_url = getattr(agent, "_default_base_url", agent.config.base_url)
        base_client = getattr(agent, "_default_model_client", agent.model)
        low_eff = effective.casefold()
        sticky_tier = getattr(agent, "_session_route_tier", "")
        if sticky_tier in ("fast", "smart") and not fresh:
            tier = sticky_tier
            is_complex = tier == "smart"
        else:
            is_complex = (
                agent.mode in ("code", "debug", "architect")
                or is_direct_skill
                or len(effective) > 320
                or any(k in low_eff for k in _COMPLEX_HINTS)
            )
            tier = "smart" if is_complex else "fast"
            agent._session_route_tier = tier
        routed_m = (smart_m or base_m) if is_complex else (fast_m or base_m)
        routed_url = str(getattr(agent.config, f"{tier}_base_url", "") or "").strip()
        routed_key = str(getattr(agent.config, f"{tier}_api_key", "") or "").strip()
        is_diff_endpoint = bool(routed_url and routed_url != base_url)
        is_local_ep = ("127.0.0.1" in routed_url) or ("localhost" in routed_url)
        if is_diff_endpoint and not routed_key and not is_local_ep:
            # Never send the primary provider's API key to a different provider endpoint!
            agent.model = base_client
            agent.config.model = base_m
            agent.config.base_url = base_url
        elif is_diff_endpoint and routed_m:
            cache = getattr(agent, "_routed_models", None)
            if cache is None:
                cache = {}
                agent._routed_models = cache
            ck = (routed_url, routed_m)
            if ck not in cache:
                routed_cfg = _dc_replace(
                    agent.config,
                    model=routed_m,
                    base_url=routed_url,
                    api_key=routed_key,
                )
                cache[ck] = agent_mod.OpenAIModel(routed_cfg)
            agent.model = cache[ck]
            agent.config.model = routed_m
            agent.config.base_url = routed_url
            agent.events.emit("model_routed", model=routed_m, tier=tier)
        else:
            agent.model = base_client
            agent.config.base_url = base_url
            target_m = routed_m or base_m
            agent.config.model = target_m
            if hasattr(agent.model, "model"):
                agent.model.model = target_m
            if target_m != base_m:
                agent.events.emit("model_routed", model=target_m, tier=tier)
    elif (fast_m or smart_m) and hasattr(agent, "_default_model_client"):
        # Routing was configured, but this turn is manual or has images -> restore default model
        agent.model = agent._default_model_client
        agent.config.model = agent._default_model_name
        agent.config.base_url = agent._default_base_url
        if hasattr(agent.model, "model"):
            agent.model.model = agent._default_model_name
