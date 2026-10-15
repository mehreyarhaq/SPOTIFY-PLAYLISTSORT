"""Headless login helpers (no network)."""

from __future__ import annotations

import pytest

from playlistsort.spotify.auth import AuthError, code_from_redirect


def test_extracts_code_from_pasted_redirect_url() -> None:
    url = "http://127.0.0.1:8888/callback?code=AQD-abc_123&state=xyz"
    assert code_from_redirect(url) == "AQD-abc_123"


def test_accepts_bare_code() -> None:
    assert code_from_redirect("AQD-abc_123") == "AQD-abc_123"


def test_rejects_error_redirect() -> None:
    with pytest.raises(AuthError, match="access_denied"):
        code_from_redirect("http://127.0.0.1:8888/callback?error=access_denied")


def test_rejects_missing_code() -> None:
    with pytest.raises(AuthError):
        code_from_redirect("http://127.0.0.1:8888/callback?state=only")
