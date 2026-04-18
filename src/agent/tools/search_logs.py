"""Loki log search tool for the OpsAgent investigation agent.

Queries the Loki HTTP API for log entries matching a text pattern,
optionally filtered by service name.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from datetime import UTC, datetime, timedelta

import requests
from langchain_core.tools import tool

logger = logging.getLogger(__name__)

LOKI_URL = "http://localhost:3100"

# Regex patterns that indicate a real crash/fault in application logs.
# Paired with a minimum-count threshold to avoid false-positive on a single
# stray warning. See _detect_crash_signal() below.
_CRASH_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bOOM[\s-]?killed\b", re.IGNORECASE),
    re.compile(r"\bkilled as a result of limit\b", re.IGNORECASE),
    re.compile(r"\bSIGKILL\b"),
    re.compile(r"\bSIGSEGV\b|\bsegmentation fault\b", re.IGNORECASE),
    re.compile(r"\bpanic\b|\bpanicked\b", re.IGNORECASE),
    re.compile(r"\bfatal\b.*(error|exception)", re.IGNORECASE),
    re.compile(r"\bstd::(?:logic|runtime)_error\b"),
    re.compile(r"\bterminate called (?:after|without)\b"),
    re.compile(r"\bunhandled exception\b", re.IGNORECASE),
    # "exit 137", "exited with code 137", "exit status: 139" — all forms.
    # Restricted to 137/139 (SIGKILL/SIGSEGV) to avoid matching benign exit 0 / 1.
    re.compile(r"\bexit(?:ed)?\b[^\n]{0,30}?\b(?:137|139)\b", re.IGNORECASE),
    re.compile(r"\blisten tcp.*(?:invalid|bind).*(?:port|address)", re.IGNORECASE),
    re.compile(r"\bcore\s*dumped\b", re.IGNORECASE),
    # Connection errors that indicate a dead dependency
    re.compile(r"\bconnection (?:refused|reset) by peer\b", re.IGNORECASE),
    re.compile(r"\bmax clients reached\b", re.IGNORECASE),
]

# Minimum crash-match count to escalate to CRITICAL. 3+ matches in a window
# filters out a single transient warning; a genuinely crashing service will
# emit many crash lines via its restart loop.
_CRASH_MIN_MATCHES = 3


def _detect_crash_signal(
    entries: list[dict], service_filter: str | None
) -> tuple[bool, int, list[str]]:
    """Scan log entries for crash/OOM/fatal patterns.

    Returns a tuple ``(is_critical, match_count, matched_excerpts)``:

    - ``is_critical``: True when the number of matching log lines reaches
      ``_CRASH_MIN_MATCHES``. One-off warnings don't cross this bar.
    - ``match_count``: how many lines matched any crash pattern.
    - ``matched_excerpts``: up to 3 representative matched log messages
      (truncated to 160 chars) for the CRITICAL note.

    ``service_filter`` is used purely as a sanity check: we only escalate
    when the caller was looking at a specific service (the escalation
    attributes the crash TO that service).
    """
    if not service_filter:
        return False, 0, []
    count = 0
    excerpts: list[str] = []
    for entry in entries:
        msg = entry.get("message", "")
        for pattern in _CRASH_PATTERNS:
            if pattern.search(msg):
                count += 1
                if len(excerpts) < 3:
                    excerpts.append(msg[:160])
                break
    return count >= _CRASH_MIN_MATCHES, count, excerpts


def _build_logql(query: str, service_filter: str | None) -> str:
    """Construct a LogQL query from a free-form pattern string.

    Handles three cases safely:

    1. **OR alternation** — ``"foo OR bar OR baz"`` → regex filter
       ``|~ ` + `(?i)(foo|bar|baz)``. LLMs often pass multi-term OR
       queries; LogQL's ``|=`` is a literal-substring filter, so we
       convert OR-style queries into a regex line filter.
    2. **Simple substring** — anything else → literal filter ``|= `term```.
    3. **Quote stripping** — embedded double quotes and backticks in the
       user's terms are stripped so they don't terminate the LogQL
       string literal. Whitespace is trimmed.

    Backticks are used for string literals because they are raw strings
    in LogQL (no escape sequences needed), which avoids the classic
    embedded-quote trap that produced 400 errors historically.
    """
    stream = f'{{service="{service_filter}"}}' if service_filter else '{job="docker"}'

    # Split on the word ``OR`` surrounded by whitespace (case-insensitive),
    # but only if there's more than one term. We strip quotes and backticks
    # from each term so they can't inject unintended LogQL syntax.
    parts = [p.strip().strip('"').strip("`") for p in re.split(r"\s+OR\s+", query)]
    parts = [p for p in parts if p]  # drop empties

    if len(parts) > 1:
        pattern = "|".join(re.escape(p) for p in parts)
        return f"{stream} |~ `(?i){pattern}`"

    # Single-term literal filter. Strip quotes/backticks from the term.
    term = query.strip().strip('"').strip("`")
    return f"{stream} |= `{term}`"


@tool
def search_logs(
    query: str,
    service_filter: str | None = None,
    time_range_minutes: int = 30,
    limit: int = 100,
    start_time: str | None = None,
) -> dict:
    """Search Loki for log entries matching a query pattern.

    Use this tool to find error messages, stack traces, or warning
    patterns in logs that corroborate or refute your hypotheses.
    Especially useful for identifying connection timeouts, OOM errors,
    or repeated retry patterns.

    Args:
        query: Text to search for in log messages. Two forms are supported:
            - **Single term** — ``"connection refused"`` or ``"OOM"`` —
              performs a literal substring match against every log line.
            - **OR alternation** — ``"error OR timeout OR panic"`` —
              matches any of the listed terms (case-insensitive). Do not
              wrap individual terms in quotes; the tool handles escaping.
        service_filter: Restrict search to a specific service name.
            If None, searches all services.
        time_range_minutes: How far back to search.  Default 30 minutes.
        limit: Maximum log entries to return.  Default 100.
        start_time: Optional ISO-8601 timestamp. When set, the query window
            is ``[start_time, start_time + time_range_minutes]`` instead of
            the default ``[now - time_range_minutes, now]``.

    Returns:
        dict with keys:

        - ``entries``: list of ``{timestamp, service, message, level}``
        - ``total_count``: number of log lines matched
        - ``error_count``: how many were ERROR/CRITICAL severity
        - ``top_patterns``: 5 most common message prefixes
        - ``crash_match_count``: how many lines matched a crash/OOM/fatal
          regex (SIGSEGV, panic, std::logic_error, exit 137/139, …)
        - ``critical_service``: populated ONLY when ``service_filter`` is
          set AND ``crash_match_count >= 3``; names the service that is
          crashing. Also adds ``anomalous: True`` and a ``note`` starting
          with ``"CRITICAL: …"`` so the evidence-scanning layer can treat
          the finding the same way it treats a metric CRITICAL signal.
    """
    try:
        if start_time:
            anchor = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
            if anchor.tzinfo is None:
                anchor = anchor.replace(tzinfo=UTC)
            # Extend window 60s before anchor to include pre-fault context
            # (matches query_metrics/discover_causation semantics).
            start = anchor - timedelta(seconds=60)
            end = min(
                anchor + timedelta(minutes=time_range_minutes),
                datetime.now(UTC),
            )
        else:
            end = datetime.now(UTC)
            start = end - timedelta(minutes=time_range_minutes)

        # Build LogQL query. Handles OR-alternation queries (common in
        # LLM-generated inputs like "error OR crash OR timeout") by
        # emitting a regex filter |~ instead of a literal |= filter.
        logql = _build_logql(query, service_filter)

        resp = requests.get(
            f"{LOKI_URL}/loki/api/v1/query_range",
            params={
                "query": logql,
                "start": str(int(start.timestamp())),
                "end": str(int(end.timestamp())),
                "limit": str(limit),
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        entries: list[dict] = []
        pattern_counter: Counter[str] = Counter()
        error_count = 0

        for stream in data.get("data", {}).get("result", []):
            stream_labels = stream.get("stream", {})
            service = stream_labels.get("service", stream_labels.get("job", "unknown"))

            for ts_ns, message in stream.get("values", []):
                ts_sec = int(ts_ns) / 1e9
                iso_ts = datetime.fromtimestamp(ts_sec, tz=UTC).isoformat()

                level = _extract_log_level(message)
                if level in ("ERROR", "CRITICAL"):
                    error_count += 1

                entries.append(
                    {
                        "timestamp": iso_ts,
                        "service": service,
                        "message": message[:500],
                        "level": level,
                    }
                )

                # Track patterns (first 80 chars as a rough pattern)
                pattern_counter[message[:80]] += 1

        top_patterns = [p for p, _ in pattern_counter.most_common(5)]

        # Escalate to CRITICAL when the results contain enough crash/OOM/fatal
        # patterns to be confidently attributed to the target service. Empty
        # service_filter → no escalation (a system-wide search can't
        # unambiguously attribute a crash to a service).
        is_critical, crash_match_count, crash_excerpts = _detect_crash_signal(
            entries, service_filter
        )

        out: dict = {
            "entries": entries,
            "total_count": len(entries),
            "error_count": error_count,
            "top_patterns": top_patterns,
            "crash_match_count": crash_match_count,
        }
        if is_critical:
            out["critical_service"] = service_filter
            out["anomalous"] = True
            out["note"] = (
                f"CRITICAL: {service_filter} logs show {crash_match_count} "
                f"crash/fault pattern matches (OOMKilled, SIGSEGV, panic, "
                f"fatal, std::logic_error, exit 137/139, or similar). "
                f"This service is crashing or has crashed recently. "
                f"Sample: {crash_excerpts[0] if crash_excerpts else 'n/a'}"
            )
        return out
    except Exception:
        logger.exception("search_logs failed for query=%s", query)
        return {
            "entries": [],
            "total_count": 0,
            "error_count": 0,
            "top_patterns": [],
            "crash_match_count": 0,
            "error": f"Failed to search logs for '{query}'",
        }


def _extract_log_level(message: str) -> str:
    """Best-effort extraction of log level from a log message."""
    upper = message.upper()
    for level in ("CRITICAL", "ERROR", "WARN", "WARNING", "INFO", "DEBUG"):
        if level in upper:
            return level if level != "WARNING" else "WARN"
    return "INFO"
