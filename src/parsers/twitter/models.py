"""Pydantic models for TwitterAPI.io responses."""

from pydantic import BaseModel


class TwitterAuthor(BaseModel):
    """Tweet author info."""

    userName: str = ""
    id: str = ""
    name: str = ""
    followers: int = 0
    following: int = 0
    isBlueVerified: bool = False
    statusesCount: int = 0
    description: str = ""

    model_config = {"extra": "ignore"}


class TwitterTweet(BaseModel):
    """Single tweet from search results."""

    id: str = ""
    text: str = ""
    likeCount: int = 0
    retweetCount: int = 0
    replyCount: int = 0
    quoteCount: int = 0
    viewCount: int = 0
    createdAt: str = ""
    lang: str = ""
    author: TwitterAuthor = TwitterAuthor()

    model_config = {"extra": "ignore"}


class TwitterSearchResult(BaseModel):
    """Aggregated search result with analytics."""

    tweets: list[TwitterTweet] = []
    total_tweets: int = 0
    kol_mentions: int = 0  # authors with 50K+ followers
    verified_mentions: int = 0  # blue verified authors
    max_likes: int = 0
    max_views: int = 0
    total_engagement: int = 0  # likes + retweets + replies + quotes
    top_kol_followers: int = 0  # highest follower count among mentioners
    has_next_page: bool = False
