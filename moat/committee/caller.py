"""API call wrapper for the three persona prompts (Sprint 5).

Deliberately not moat/analysis/caller.py's call_sync(): that function is
built around fetching filing sections and the citations API feature, neither
of which applies here (see prompt.py's module docstring). This is a much
thinner sync-only wrapper — reused from moat/analysis/ is only pricing.py's
cost math (fully generic, no filing coupling) and the same call shape
(client.messages.stream, usage dict, cost estimate) for consistency.

Sync only, matching the plan's decision to pilot a 69-company/3-call
surface directly rather than build Sprint 3's batch machinery for a run this
size — batch is a fast-follow if a full run's wall-clock time warrants it,
not built preemptively here.
"""
from __future__ import annotations

import dataclasses
import hashlib

from moat.analysis.pricing import DEFAULT_MODEL, estimate_cost
from moat.committee.prompt import PROTOCOL_VERSION, build_request


@dataclasses.dataclass
class PersonaCallResult:
    ticker: str
    persona: str
    model_id: str
    stop_reason: str
    raw_text: str
    usage: dict
    cost_estimate: float
    prompt_sha256: str
    protocol_version: str


def _prompt_sha256(system: str, user_message: str) -> str:
    h = hashlib.sha256()
    h.update(system.encode("utf-8"))
    h.update(b"\x00")
    h.update(user_message.encode("utf-8"))
    return h.hexdigest()


def call_persona(
    client,
    ticker: str,
    persona: str,
    context_block: str,
    model_id: str = DEFAULT_MODEL,
    dry_run: bool = False,
) -> PersonaCallResult:
    system, user_message = build_request(persona, ticker, context_block)
    prompt_sha = _prompt_sha256(system, user_message)

    if dry_run:
        resp = client.messages.count_tokens(
            model=model_id,
            system=system,
            messages=[{"role": "user", "content": user_message}],
        )
        return PersonaCallResult(
            ticker=ticker, persona=persona, model_id=model_id, stop_reason="dry_run",
            raw_text="", usage={"input_tokens": resp.input_tokens}, cost_estimate=0.0,
            prompt_sha256=prompt_sha, protocol_version=PROTOCOL_VERSION,
        )

    with client.messages.stream(
        model=model_id,
        max_tokens=4_096,
        system=system,
        messages=[{"role": "user", "content": user_message}],
    ) as stream:
        message = stream.get_final_message()

    if message.stop_reason == "refusal":
        return PersonaCallResult(
            ticker=ticker, persona=persona, model_id=model_id, stop_reason="refusal",
            raw_text="",
            usage={
                "input_tokens": message.usage.input_tokens,
                "output_tokens": message.usage.output_tokens,
            },
            cost_estimate=0.0, prompt_sha256=prompt_sha, protocol_version=PROTOCOL_VERSION,
        )

    raw_text = "".join(
        block.text for block in message.content if getattr(block, "type", None) == "text"
    )
    usage_dict = {
        "input_tokens":                message.usage.input_tokens,
        "output_tokens":               message.usage.output_tokens,
        "cache_creation_input_tokens": getattr(message.usage, "cache_creation_input_tokens", 0),
        "cache_read_input_tokens":     getattr(message.usage, "cache_read_input_tokens", 0),
    }
    cost = estimate_cost(usage_dict, model_id, is_batch=False)

    return PersonaCallResult(
        ticker=ticker, persona=persona, model_id=model_id, stop_reason=message.stop_reason,
        raw_text=raw_text, usage=usage_dict, cost_estimate=cost,
        prompt_sha256=prompt_sha, protocol_version=PROTOCOL_VERSION,
    )
