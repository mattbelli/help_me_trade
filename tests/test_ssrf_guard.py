"""SSRF guard for fetch_url: validate URLs before any HTTP request."""

from __future__ import annotations

import socket
from unittest.mock import MagicMock, patch

import pytest

from ai.research.sources import _is_fetch_url_safe, fetch_url

_PUBLIC_IP = "93.184.216.34"
_PRIVATE_IP = "10.0.0.5"
_LOOPBACK_IP = "127.0.0.1"
_LINK_LOCAL_IP = "169.254.169.254"
_IPV6_LOOPBACK = "::1"


def _mock_getaddrinfo(host: str, port: int, *, ip: str) -> list[tuple]:
    return [
        (
            socket.AF_INET if ":" not in ip else socket.AF_INET6,
            socket.SOCK_STREAM,
            6,
            "",
            (ip, port),
        )
    ]


def _addrinfo_side_effect(ip: str):
    def side_effect(host, port, *args, **kwargs):
        return _mock_getaddrinfo(host, port, ip=ip)

    return side_effect


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/x",
        "http://localhost/x",
        "http://127.0.0.1/x",
        f"http://{_LINK_LOCAL_IP}/latest/meta-data",
        f"http://{_PRIVATE_IP}/x",
        f"http://[{_IPV6_LOOPBACK}]/x",
    ],
)
def test_is_fetch_url_safe_blocks_unsafe_urls(url: str) -> None:
    with patch("ai.research.sources.socket.getaddrinfo") as mock_getaddrinfo:
        mock_getaddrinfo.side_effect = _addrinfo_side_effect(_PUBLIC_IP)
        assert _is_fetch_url_safe(url) is False


def test_is_fetch_url_safe_blocks_hostname_resolving_to_private_ip() -> None:
    with patch(
        "ai.research.sources.socket.getaddrinfo",
        side_effect=_addrinfo_side_effect(_PRIVATE_IP),
    ):
        assert _is_fetch_url_safe("http://innocent-looking.example/x") is False


def test_is_fetch_url_safe_blocks_on_dns_failure() -> None:
    with patch(
        "ai.research.sources.socket.getaddrinfo",
        side_effect=OSError("name resolution failed"),
    ):
        assert _is_fetch_url_safe("http://does-not-resolve.example/x") is False


def test_is_fetch_url_safe_allows_public_url() -> None:
    with patch(
        "ai.research.sources.socket.getaddrinfo",
        side_effect=_addrinfo_side_effect(_PUBLIC_IP),
    ):
        assert _is_fetch_url_safe("https://example.com/path") is True


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/x",
        "http://localhost/x",
        "http://127.0.0.1/x",
        f"http://{_LINK_LOCAL_IP}/latest/meta-data",
        f"http://{_PRIVATE_IP}/x",
        f"http://[{_IPV6_LOOPBACK}]/x",
    ],
)
def test_fetch_url_blocks_unsafe_urls_without_http(url: str) -> None:
    with patch("ai.research.sources.httpx.Client") as mock_client_cls:
        assert fetch_url(url) == ""
        mock_client_cls.assert_not_called()


def test_fetch_url_blocks_hostname_resolving_to_private_ip() -> None:
    with (
        patch(
            "ai.research.sources.socket.getaddrinfo",
            side_effect=_addrinfo_side_effect(_PRIVATE_IP),
        ),
        patch("ai.research.sources.httpx.Client") as mock_client_cls,
    ):
        assert fetch_url("http://innocent-looking.example/x") == ""
        mock_client_cls.assert_not_called()


def test_fetch_url_allows_public_url() -> None:
    html = "<html><body><p>Hello world</p></body></html>"
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = html
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = mock_resp

    with (
        patch(
            "ai.research.sources.socket.getaddrinfo",
            side_effect=_addrinfo_side_effect(_PUBLIC_IP),
        ),
        patch("ai.research.sources.httpx.Client", return_value=mock_client),
    ):
        result = fetch_url("https://example.com/page")

    assert result == "Hello world"
    mock_client.get.assert_called_once_with(
        "https://example.com/page",
        headers={"User-Agent": "HelpMeTrade-Research/1.0"},
    )


def test_fetch_url_blocks_redirect_to_internal_host() -> None:
    public_url = "https://example.com/start"
    internal_url = f"http://{_LINK_LOCAL_IP}/latest/meta-data"

    redirect_resp = MagicMock()
    redirect_resp.status_code = 302
    redirect_resp.headers = {"Location": internal_url}

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = redirect_resp

    def getaddrinfo_side_effect(host, port, *args, **kwargs):
        if host == "example.com":
            return _mock_getaddrinfo(host, port, ip=_PUBLIC_IP)
        return _mock_getaddrinfo(host, port, ip=_LINK_LOCAL_IP)

    with (
        patch(
            "ai.research.sources.socket.getaddrinfo",
            side_effect=getaddrinfo_side_effect,
        ),
        patch("ai.research.sources.httpx.Client", return_value=mock_client),
    ):
        result = fetch_url(public_url)

    assert result == ""
    mock_client.get.assert_called_once_with(
        public_url,
        headers={"User-Agent": "HelpMeTrade-Research/1.0"},
    )
