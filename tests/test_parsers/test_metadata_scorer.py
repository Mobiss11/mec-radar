"""Tests for token metadata scoring."""

from src.parsers.metadata_scorer import score_metadata


class TestMetadataScorer:
    def test_full_socials(self) -> None:
        """All socials present + good description = max score."""
        result = score_metadata(
            description="A comprehensive decentralized finance protocol for yield farming",
            website="https://example.com",
            twitter="@example_token",
            telegram="t.me/example_group",
        )
        assert result.score == 9  # 2(desc) + 3(web) + 2(tw) + 2(tg)
        assert result.socials_count == 3
        assert result.has_description is True

    def test_no_socials(self) -> None:
        """No socials at all = negative score."""
        result = score_metadata()
        assert result.score == -3
        assert result.socials_count == 0

    def test_partial_socials(self) -> None:
        """Only twitter present."""
        result = score_metadata(twitter="@some_token")
        assert result.has_twitter is True
        assert result.has_website is False
        assert result.socials_count == 1
        assert result.score == 2  # just twitter bonus

    def test_generic_description_penalty(self) -> None:
        """Generic description should get penalty."""
        result = score_metadata(
            description="this is just a token nothing more",
            website="https://example.com",
        )
        assert result.score == 2  # -1(generic) + 3(web) = 2

    def test_short_description_ignored(self) -> None:
        """Description shorter than 20 chars is not counted."""
        result = score_metadata(
            description="short",
            website="https://example.com",
        )
        assert result.has_description is False
        assert result.score == 3  # only website
