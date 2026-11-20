"""HTTPS fallback for Windows machines whose Python SSL stack fails.

Use the system curl's normal certificate verification.  Auth headers are sent
through stdin, not on the process command line, and are never printed.
"""
from __future__ import annotations

import os
import re
import shutil
import ssl
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

_TLS_WORDS = ("ssl", "tls", "certificate verify failed", "certificate error", "handshake")


def _is_tls_error(exc):
    if isinstance(exc, ssl.SSLError):
        return True
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", None)
        if isinstance(reason, ssl.SSLError):
            return True
        # Some Anaconda builds wrap the SSL failure in a generic OSError.
        return any(word in str(reason).lower() for word in _TLS_WORDS)
    return False


def _curl_binary():
    return shutil.which("curl.exe") or shutil.which("curl")


def _system_proxy_for(url):
    """Return a credential-free system proxy suitable for curl.

    Python's urllib reads the Windows Internet Settings proxy, while the
    Windows curl build does not.  Reusing that setting keeps both transports
    on the same network path.  Credential-bearing proxy URLs are deliberately
    ignored so secrets never appear in curl's process arguments.
    """
    parsed_url = urllib.parse.urlsplit(url)
    if not parsed_url.hostname or urllib.request.proxy_bypass(parsed_url.hostname):
        return None
    proxy = urllib.request.getproxies().get(parsed_url.scheme.lower())
    if not proxy:
        return None
    candidate = proxy if "://" in proxy else "http://" + proxy
    parsed_proxy = urllib.parse.urlsplit(candidate)
    if (parsed_proxy.scheme not in ("http", "https", "socks4", "socks4a", "socks5", "socks5h")
            or not parsed_proxy.hostname or parsed_proxy.username is not None
            or parsed_proxy.password is not None):
        return None
    return candidate


def curl_https(url, headers, timeout=25, data=None):
    """Return HTTP status and raw bytes, with verified TLS.

    Using ``--header @-`` keeps API keys out of curl's command line and logs.
    Only HTTPS is accepted, including across redirects.
    """
    if not isinstance(url, str) or not url.lower().startswith("https://"):
        raise ValueError("curl fallback only accepts HTTPS URLs")
    curl = _curl_binary()
    if not curl:
        raise OSError("curl fallback unavailable: Windows curl.exe not found")
    header_lines = []
    for name, value in headers.items():
        name, value = str(name), str(value)
        if not re.fullmatch(r"[A-Za-z0-9!#$%&'*+.^_`|~-]+", name):
            raise ValueError("invalid HTTP header name")
        if "\r" in value or "\n" in value:
            raise ValueError("invalid HTTP header value")
        header_lines.append(f"{name}: {value}\r\n")
    # The request body is never logged, nor are credentials passed as argv.
    with tempfile.TemporaryDirectory(prefix="urbanflow_curl_") as tmp:
        dst = Path(tmp) / "response.bin"
        command = [curl, "--silent", "--show-error",
                   "--proto", "=https", "--proto-redir", "=https",
                   "--connect-timeout", "12",
                   "--max-time", str(max(15, timeout + 10)),
                   "--output", str(dst), "--write-out", "%{http_code}",
                   "--header", "@-"]
        proxy = _system_proxy_for(url)
        if proxy:
            command.extend(["--proxy", proxy])
        if data is not None:
            body_path = Path(tmp) / "request.bin"
            body_path.write_bytes(data)
            command.extend(["--data-binary", "@" + str(body_path)])
        # Keep API credentials on their original HTTPS host, without redirects.
        if not any(name.lower() in ("apikey", "authorization", "x-api-key") for name in headers):
            command.extend(["--location", "--max-redirs", "4"])
        command.append(url)
        kwargs = {"input": "".join(header_lines).encode("utf-8"),
                  "stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
                  "timeout": max(20, timeout + 15), "check": False}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            process = subprocess.run(command, **kwargs)
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError("curl fallback timed out") from exc
        if process.returncode != 0:
            # Never include curl's stderr, since a remote URL might contain secrets.
            labels = {6: "DNS lookup failed", 7: "connection failed", 28: "timeout",
                      35: "TLS handshake failed", 60: "certificate validation failed",
                      77: "CA certificate problem"}
            msg = labels.get(process.returncode, "network request failed")
            raise OSError(f"curl fallback: {msg} (exit={process.returncode})")
        code = process.stdout.decode("ascii", errors="replace").strip()
        if not re.fullmatch(r"\d{3}", code):
            raise OSError("curl fallback: missing HTTP status")
        return int(code), dst.read_bytes()


def open_https(url, headers, timeout=25, prefer_curl=False):
    """Open an HTTPS URL, optionally preferring curl for compatibility."""
    if prefer_curl:
        return curl_https(url, headers, timeout=timeout)
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, response.read()
    except (ssl.SSLError, urllib.error.URLError) as exc:
        if not _is_tls_error(exc):
            raise
        return curl_https(url, headers, timeout=timeout)
