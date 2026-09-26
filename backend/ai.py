"""Local-LLM bridge for SAT-SA (Ollama).

Design constraints for the supervisory enclave:
- **No new dependencies**: talks to Ollama with the standard library only
  (`http.client`). The backend image stays lean and pip-installable offline.
- **Ollama never faces the browser.** The dashboard calls the backend, and the
  backend is the single component allowed to reach the local model runtime.
- **Everything is opt-in via environment.** If OLLAMA_ENABLED is not set the
  AI surface reports "unconfigured" instead of erroring, so deployments
  without a model keep working unchanged.
"""

from __future__ import annotations

import http.client
import json
import os
import socket
from typing import Any, Dict, Iterator, List, Optional

# --------------------------------------------------------------------------- config

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")  # e.g. http://ollama:11434
# Default is the 3B model: the 1B variant answers ~2x faster on CPU but invents
# fields and reasons poorly over numeric snapshots (measured), which is worse
# than slow for a supervisory tool. Latency-sensitive deployments can set
# OLLAMA_MODEL=llama3.2:1b and accept the quality drop.
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:3b")
OLLAMA_ENABLED = os.getenv("OLLAMA_ENABLED", "false").lower() in ("1", "true", "yes", "on")
# Two very different clocks:
# - CONNECT timeout: how long we wait for the TCP connection + response headers
#   (a healthy runtime answers /api/chat headers immediately — it streams).
# - GENERATION budget: how long the model may keep producing tokens. On CPU a
#   large prompt can spend minutes in pre-fill before the first token, so this
#   must be generous; it bounds a *hung* stream, not a busy model.
OLLAMA_CONNECT_TIMEOUT = float(os.getenv("OLLAMA_CONNECT_TIMEOUT", "10"))
OLLAMA_GENERATION_TIMEOUT = float(os.getenv("OLLAMA_GENERATION_TIMEOUT", "300"))
# How long Ollama keeps the model in memory after the last request. The default
# (5 min) means an idle console reloads the 2 GB model on every question —
# tens of seconds of pure latency. 30 min keeps it warm.
OLLAMA_KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", "30m")
# Context window handed to the model. Smaller window = dramatically faster
# CPU pre-fill. 2048 tokens fits the compact snapshot the UI sends plus the
# answer; raise only together with OLLAMA_NUM_THREADS on faster hardware.
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "2048"))
# CPU threads for inference. Physical cores (not SMT threads) are usually
# fastest for llama.cpp; 0 lets Ollama decide.
OLLAMA_NUM_THREADS = int(os.getenv("OLLAMA_NUM_THREADS", "0"))
# Kept for backwards compatibility; no longer used as the per-socket timeout.
OLLAMA_TIMEOUT = float(os.getenv("OLLAMA_TIMEOUT", "300"))

_SYSTEM_PROMPT = """You are a senior SOC analyst reviewing SAT-SA supervisory analytics for India's \
critical sector entities (SIH 26157, NTRO/NCIIPC). A colleague sent you the snapshot below and \
asked you a question. Respond like a knowledgeable colleague talking to another analyst: analyse, \
explain your reasoning in plain prose, and give a bottom line. Never dump rows or repeat the \
snapshot verbatim — the reader already has it.

How the scoring works (use this, do not guess):
- composite = 0.40×execution_gap + 0.35×negative_space + 0.15×trend_deterioration + 0.10×peer_deviation.
- execution_gap: controls look nominal on paper but triage was superficial — alerts closed fast \
without matching investigation depth.
- negative_space: expected evidence is MISSING entirely — assets or events that should appear in \
the submission but don't.
- trend_deterioration: scores worsening across reporting periods. peer_deviation: unexplained \
variation vs peer entities in the same sector.
- Bands: Very High ≥75, High ≥50, Moderate ≥25, else Low.

Rules of evidence:
- Cite only figures present in the snapshot. If a needed figure is absent, say what you would \
need and why — do not invent names, fields, or numbers.
- Show the arithmetic when you attribute a band (e.g. which component contributes most points \
to the composite).
- Distinguish clearly between what the data shows and what you infer from it. It is fine to say \
"the snapshot doesn't show X".
- If the question asks about the top-ranked entity, reason from its actual component scores.
- Keep it conversational but precise; a few short paragraphs at most unless asked for more."""

# A conservative size guard: Ollama truncates its context window anyway, but we
# fail fast on obviously oversized prompts instead of hanging a long request.
# 4096-token context ≈ ~14k chars of English for llama-class tokenizers; keep
# the snapshot well under it so pre-fill stays fast and nothing is truncated.
_MAX_PROMPT_CHARS = 12_000


class OllamaUnavailable(RuntimeError):
    """Raised when Ollama is disabled, unreachable, or returns an error status."""


class OllamaTimeout(OllamaUnavailable):
    """The runtime accepted the request but the generation budget ran out.
    Distinct from "unreachable": the model was working, the caller (UI) should
    suggest shortening the prompt or increasing OLLAMA_GENERATION_TIMEOUT."""


def _parse_host(host: str) -> tuple[str, int, str]:
    if "://" not in host:
        host = "http://" + host
    from urllib.parse import urlsplit

    parts = urlsplit(host)
    return parts.hostname or "127.0.0.1", parts.port or 11434, f"http://{parts.hostname or '127.0.0.1'}:{parts.port or 11434}"


def _request(
    path: str,
    method: str = "GET",
    body: Optional[Dict[str, Any]] = None,
    stream: bool = False,
    timeout: Optional[float] = None,
) -> Any:
    """Perform one HTTP request against Ollama.

    Returns the parsed JSON for normal calls, or the raw response iterator for
    streaming calls (caller closes it)."""
    if not OLLAMA_ENABLED:
        raise OllamaUnavailable("Ollama integration is disabled (OLLAMA_ENABLED is not set).")

    host, port, base = _parse_host(OLLAMA_HOST)
    # Ollama does NOT send response headers until the prompt pre-fill finishes
    # and the first token is ready — so for streaming calls even the header
    # wait can legitimately take minutes on CPU. Use the generation budget for
    # the whole socket lifetime; a dead runtime still fails immediately via
    # TCP connection-refused, and a hung stream dies at the budget.
    effective_timeout = (OLLAMA_CONNECT_TIMEOUT if timeout is None else timeout) if not stream else OLLAMA_GENERATION_TIMEOUT
    try:
        conn = http.client.HTTPConnection(host, port, timeout=effective_timeout)
        headers = {"Content-Type": "application/json"}
        payload = None
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
        conn.request(method, path, body=payload, headers=headers)
        resp = conn.getresponse()
    except socket.timeout as exc:
        # socket.timeout subclasses OSError, so this must come first. A timeout
        # in ANY phase (connect is instant; header-wait includes the model's
        # whole pre-fill) means the runtime is alive but too slow / wedged.
        raise OllamaTimeout(
            f"Ollama at {base} accepted the connection but did not answer within "
            f"the {effective_timeout:.0f}s budget ({exc}). The model is likely "
            f"pre-filling a long prompt on CPU — shorten the prompt or raise "
            f"OLLAMA_GENERATION_TIMEOUT."
        ) from exc
    except (OSError, http.client.HTTPException) as exc:
        # Connection refused / reset / DNS failure — the runtime is genuinely
        # not there.
        raise OllamaUnavailable(f"Ollama is unreachable at {base}: {exc}") from exc

    if resp.status >= 400:
        detail = resp.read(2000).decode("utf-8", "replace")
        conn.close()
        raise OllamaUnavailable(f"Ollama returned HTTP {resp.status}: {detail}")

    if stream:
        return resp  # caller drives resp and conn lifetime

    try:
        data = json.loads(resp.read().decode("utf-8"))
    finally:
        conn.close()
    return data


def fit_context(context: str, max_chars: int = _MAX_PROMPT_CHARS) -> str:
    """Trim an analytics snapshot so the whole prompt fits the model's context
    window. Prefers keeping the header block (run id, counts) plus the top of
    every section, because callers rank rows by importance before sending."""
    if len(context) <= max_chars:
        return context
    # Simple deterministic halving: keep the head (metadata + top rows) and the
    # tail (data-quality summary), drop the middle rows.
    head = max_chars * 3 // 4
    tail = max_chars - head - 40
    return context[:head] + "\n… [snapshot truncated to fit the model context] …\n" + context[-tail:]


# --------------------------------------------------------------------------- public API


def get_status() -> Dict[str, Any]:
    """Report configuration and live model availability. Never raises."""
    configured = OLLAMA_ENABLED
    base = _parse_host(OLLAMA_HOST)[2]
    reachable = False
    models: List[Dict[str, Any]] = []
    error: Optional[str] = None

    if configured:
        try:
            data = _request("/api/tags")
            reachable = True
            raw = data.get("models", []) or []
            models = [
                {
                    "name": m.get("name") or m.get("model") or "?",
                    "size_bytes": m.get("size"),
                    "parameter_size": (m.get("details") or {}).get("parameter_size"),
                    "quantization": (m.get("details") or {}).get("quantization_level"),
                }
                for m in raw
            ]
        except OllamaUnavailable as exc:
            error = str(exc)
        except Exception as exc:  # never let status crash the caller
            error = f"status probe failed: {exc}"

    default_model_ready = any(m["name"] == OLLAMA_MODEL for m in models)

    return {
        "configured": configured,
        "reachable": reachable,
        "host": base if configured else None,
        "model": OLLAMA_MODEL,
        "default_model_ready": default_model_ready,
        "models": models,
        "error": error,
    }


def stream_chat(messages: List[Dict[str, str]]) -> Iterator[str]:
    """Yield text deltas from Ollama's /api/chat, decoding NDJSON on the fly."""
    if not messages:
        raise OllamaUnavailable("No messages provided.")

    options: Dict[str, Any] = {
        "temperature": 0.2,
        "num_ctx": OLLAMA_NUM_CTX,
    }
    if OLLAMA_NUM_THREADS > 0:
        options["num_thread"] = OLLAMA_NUM_THREADS
    body = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": True,
        "keep_alive": OLLAMA_KEEP_ALIVE,
        "options": options,
    }
    try:
        resp = _request("/api/chat", method="POST", body=body, stream=True)
    except OllamaTimeout:
        raise  # already carries the right diagnosis
    except OllamaUnavailable as exc:
        # Only reclassify as a timeout when the underlying cause really was a
        # socket timeout (slow/wedged model). Connection-refused stays an
        # honest "unreachable".
        if isinstance(exc.__cause__, socket.timeout):
            raise OllamaTimeout(
                f"No first token within {OLLAMA_GENERATION_TIMEOUT:.0f}s — the model is "
                f"likely still pre-filling a long prompt on CPU. Shorten the prompt "
                f"or raise OLLAMA_GENERATION_TIMEOUT."
            ) from exc
        raise
    try:
        for raw_line in resp:
            line = raw_line.strip()
            if not line:
                continue
            try:
                event = json.loads(line.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if event.get("error"):
                raise OllamaUnavailable(str(event["error"]))
            piece = (event.get("message") or {}).get("content", "")
            if piece:
                yield piece
            if event.get("done"):
                return
    except socket.timeout as exc:
        # The stream went silent for the whole generation budget — treat the
        # runtime as hung rather than reporting it as "unreachable".
        raise OllamaTimeout(
            f"The model stopped producing tokens for {OLLAMA_GENERATION_TIMEOUT:.0f}s "
            f"and the stream was abandoned. Long CPU prompts may need a larger "
            f"OLLAMA_GENERATION_TIMEOUT or a shorter snapshot."
        ) from exc
    finally:
        resp.close()
        try:
            resp.sock and resp.sock.close()
            resp._connection and resp._connection.close()  # type: ignore[attr-defined]
        except Exception:
            pass


def summarize_findings_context(payload: Dict[str, Any], max_chars: int = _MAX_PROMPT_CHARS) -> str:
    """Render the analytics payload into a compact, deterministic text block
    suitable for a small local model's context window."""
    lines: List[str] = ["SAT-SA ANALYTICS SNAPSHOT"]

    counts = payload.get("record_counts") or {}
    if counts:
        lines.append(
            "Dataset: " + ", ".join(f"{k}={v}" for k, v in counts.items() if isinstance(v, int))
        )
    if payload.get("run_id"):
        lines.append(f"Run: {payload['run_id']}")
    if payload.get("dataset_hash"):
        lines.append(f"Dataset SHA-256: {payload['dataset_hash']}")

    scores = payload.get("entity_scores") or []
    if scores:
        lines.append("")
        lines.append("ENTITY RISK SCORES (rank, entity, sector, band, composite, execution gap, negative space, trend, peer deviation, data quality):")
        for s in scores:
            lines.append(
                f"- #{s.get('rank')} {s.get('entity_name')} ({s.get('entity_id')}) [{s.get('sector')}] "
                f"band={s.get('prioritization_band')} composite={s.get('overall_risk_score')} "
                f"exec={s.get('execution_gap_score')} neg={s.get('negative_space_score')} "
                f"trend={s.get('trend_deterioration_score')} peer={s.get('unexplained_peer_deviation_score')} "
                f"dq={s.get('data_quality_score')} confidence={s.get('confidence_label')}"
            )

    findings = payload.get("findings") or []
    if findings:
        lines.append("")
        lines.append(f"FINDINGS ({len(findings)}):")
        for f in findings:
            lines.append(
                f"- {f.get('finding_id')} [{f.get('severity')}] {f.get('entity_id')} "
                f"rule={f.get('rule_id')} score={f.get('score')} class={f.get('finding_class')}: "
                f"{f.get('rationale')}"
            )

    queue = payload.get("review_queue") or []
    if queue:
        lines.append("")
        lines.append(f"REVIEW QUEUE ({len(queue)} open items):")
        for q in queue:
            details = q.get("details") or {}
            lines.append(
                f"- {q.get('queue_item_id')} [{q.get('priority_band')}] {q.get('entity_name')} "
                f"({q.get('entity_id')}) priority={q.get('priority_score')} status={q.get('review_status')} "
                f"escalated={details.get('is_escalated')} evidence={q.get('evidence_summary')}"
            )

    dq = payload.get("data_quality") or {}
    if dq:
        lines.append("")
        lines.append(
            f"DATA QUALITY: overall={dq.get('overall_score')}, duplicates={dq.get('duplicate_ids_count')}, "
            f"missing_timestamps={dq.get('missing_timestamps_count')}, orphans={dq.get('orphan_cases_count') + dq.get('orphan_escalations_count', 0)}, "
            f"rejected={dq.get('rejected_records_count')}"
        )
        for issue in (dq.get("issues") or [])[:12]:
            lines.append(f"  * [{issue.get('severity')}] {issue.get('rule_name')}: {issue.get('description')}")

    text = "\n".join(str(l) for l in lines)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n... (truncated)"
    return text
