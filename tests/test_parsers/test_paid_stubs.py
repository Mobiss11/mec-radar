"""Tests for paid API clients (Twitter)."""

import pytest

from src.parsers.twitter.client import TwitterClient


class TestTwitterClient:
    def test_import(self) -> None:
        from src.parsers.twitter import client  # noqa: F401

    def test_creates_client(self) -> None:
        client = TwitterClient(api_key="test-key")
        assert client is not None

    def test_build_query_with_symbol(self) -> None:
        client = TwitterClient(api_key="test-key")
        query = client._build_query("BONK", None, None)
        assert '"$BONK"' in query
        assert "-filter:retweets" in query

    def test_build_query_skips_generic_symbols(self) -> None:
        client = TwitterClient(api_key="test-key")
        query = client._build_query("SOL", None, None)
        assert '"$SOL"' not in query
