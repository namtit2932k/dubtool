"""Connectivity and clock-offset checks (AGENT_UPDATE_8.md 2.2, diagnostics
7.2).

The hosts are discovered, not hardcoded: the edge-tts endpoint comes from the
installed package's constants, and the Gemini/HF hosts are the API/document
hosts named in this module next to a comment.

`check_hosts()` runs DNS + TLS reachability per host, plus one HTTPS `Date`
header comparison for the clock offset. It never changes the clock and never
prints, logs, or returns any key material.
"""

from __future__ import annotations

import email.utils
import logging
import socket
import ssl
import urllib.request

log = logging.getLogger("dubtool")

GEMINI_HOST = "generativelanguage.googleapis.com"  # google-genai SDK endpoint
HF_HOST = "huggingface.co"                          # Whisper model downloads


def edge_tts_hosts() -> list[str]:
    """Hosts the installed edge_tts package connects to, read from its own
    constants (never hardcoded here). Empty when discovery fails."""
    hosts: list[str] = []
    try:
        import edge_tts.constants as c
    except Exception:
        return hosts
    for name in dir(c):
        if "URL" not in name:
            continue
        value = getattr(c, name, "")
        if not isinstance(value, str):
            continue
        for prefix in ("https://", "wss://"):
            if value.startswith(prefix):
                host = value.split("//", 1)[1].split("/", 1)[0].split(":", 1)[0]
                if host and host not in hosts:
                    hosts.append(host)
    return hosts


def check_hosts(timeout_s: float = 10.0) -> list[dict]:
    """One row per host: dns, tls, and (once) clock_offset_s from an HTTPS
    response's Date header."""
    hosts: dict[str, str] = {GEMINI_HOST: "gemini api", HF_HOST: "whisper models"}
    for host in edge_tts_hosts():
        hosts.setdefault(host, "edge-tts")

    rows: list[dict] = []
    clock_measured = False
    for api_host, purpose in hosts.items():
        row: dict = {"host": api_host, "purpose": purpose, "dns": "fail", "tls": "fail"}
        try:
            socket.gethostbyname(api_host)
            row["dns"] = "ok"
        except OSError:
            rows.append(row)
            continue
        try:
            with socket.create_connection((api_host, 443), timeout=timeout_s) as netsock:
                with ssl.create_default_context().wrap_socket(
                    netsock, server_hostname=api_host
                ):
                    row["tls"] = "ok"
        except OSError:
            rows.append(row)
            continue
        if not clock_measured:
            offset = clock_offset(api_host, timeout_s)
            if offset is not None:
                row["clock_offset_s"] = round(offset, 2)
                clock_measured = True
        rows.append(row)
    return rows


def clock_offset(host: str, timeout_s: float = 10.0) -> float | None:
    """Local time minus the response `Date` header (seconds). None when no
    HTTPS Date header is available (never changes the clock)."""
    import datetime as _dt

    try:
        request = urllib.request.Request(
            f"https://{host}/", headers={"User-Agent": "dubtool-netcheck"}, method="HEAD"
        )
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            date_value = response.headers.get("Date")
    except Exception:
        return None
    if not date_value:
        return None
    try:
        server = email.utils.parsedate_to_datetime(date_value)
    except (TypeError, ValueError):
        return None
    if server.tzinfo is None:
        return None
    return _dt.datetime.now(_dt.timezone.utc).timestamp() - server.timestamp()


# AGU9 10.7: the last measured offset is remembered so the TTS-unavailable
# dialog and the diagnostics can add the clock hint. The key is the host it
# came from; the value is seconds (local minus server).
RECORDED_OFFSETS: dict[str, float] = {}


def record_offset(host: str, seconds: float) -> None:
    if isinstance(seconds, (int, float)):
        RECORDED_OFFSETS[host] = float(seconds)


def large_offset() -> float | None:
    """The most negative-or-positive offset ever recorded this session with
    magnitude over 30 s (None when every measurement is inside 30 s)."""
    for value in RECORDED_OFFSETS.values():
        if abs(value) > 30.0:
            return value
    return None


def format_table(rows: list[dict], elapsed_s: float | None = None) -> str:
    header = f"Kết nối mạng (DNS + TLS){f' ({elapsed_s:.1f}s)' if elapsed_s is not None else ''}:"
    lines = [
        header,
        "  host                          purpose        dns  tls  clock offset",
    ]
    for row in rows:
        offset = f"{row['clock_offset_s']:+.2f}s" if "clock_offset_s" in row else "-"
        lines.append(
            f"  {row['host']:<30}{row['purpose']:<15}{row['dns']:<5}{row['tls']:<5}{offset}"
        )
    return "\n".join(lines)


def print_table(rows: list[dict], elapsed_s: float | None = None) -> None:
    print(format_table(rows, elapsed_s))
