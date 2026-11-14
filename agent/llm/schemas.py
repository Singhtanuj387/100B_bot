from typing import List, Optional
from pydantic import BaseModel, Field


class JudgeResult(BaseModel):
    """Structured assessment returned by Claude Vision video judge."""
    funny_score: float = Field(
        ...,
        ge=1.0,
        le=10.0,
        description="Comedic score from 1.0 (not funny at all) to 10.0 (hilarious / viral).",
    )
    is_english_or_no_speech: bool = Field(
        ...,
        description="True if video has English speech or no speech / universal visual humor. False if foreign dialogue is critical to the joke.",
    )
    is_safe_for_ads: bool = Field(
        ...,
        description="True if safe for YouTube monetization. False if contains severe injury, blood/gore, hate speech, or harassment.",
    )
    has_visible_watermark: bool = Field(
        ...,
        description="True if has prominent third-party logos, competitor channel handles, or heavy intrusive watermarks.",
    )
    is_broadcast_or_movie: bool = Field(
        default=False,
        description="True if clip appears to be from a TV show, broadcast news, late-night talk show interview, movie, sitcom, or studio production (high risk of YouTube Content ID copyright strike). False for organic, real-world user-generated clips.",
    )
    reason: str = Field(
        ...,
        max_length=250,
        description="Concise one-line rationale explaining the funny score and decision.",
    )


class CaptionResult(BaseModel):
    """Structured output for viral captions and YouTube metadata."""
    caption: str = Field(
        ...,
        description="Punchy social caption for video overlay. Max 8 words, conversational, natural humor, max 1 emoji, NO hashtags, NO links, NO usernames.",
    )
    youtube_title: str = Field(
        ...,
        max_length=100,
        description="Engaging YouTube Shorts title with 1-2 relevant hashtags.",
    )
    youtube_description: str = Field(
        ...,
        description="YouTube Shorts description including credit to original creator/source URL and channel disclaimer.",
    )


class PlanResult(BaseModel):
    """Structured search plan for video discovery loop."""
    subreddits: List[str] = Field(
        default_factory=list,
        description="Target subreddits chosen for this iteration.",
    )
    twitter_accounts: List[str] = Field(
        default_factory=list,
        description="Target X accounts chosen for this iteration.",
    )
    strategy_reason: str = Field(
        ...,
        description="Reasoning explaining why these sources were selected based on previous rejection trends.",
    )
    proposed_new_sources: List[str] = Field(
        default_factory=list,
        description="Optional new sources outside the allowed list that require human approval before scraping.",
    )


# --- Phase 3.5 Discovery & Calibration Schemas ---

class DiscoveryItem(BaseModel):
    """Candidate subreddit or search query suggested by LLM discovery."""
    source_type: str = Field(
        ...,
        description="'subreddit' or 'search_query'",
    )
    name: str = Field(
        ...,
        description="Subreddit name (e.g. 'ContagiousLaughter') or search query (e.g. 'unexpected comedy fails')",
    )
    listing_mode: str = Field(
        default="hot",
        description="'hot', 'top_day', 'top_week', 'top_month', 'top_year', 'top_all', or 'rising'",
    )
    reason: str = Field(
        ...,
        description="Why this source is expected to yield short, funny English/no-speech comedy clips.",
    )


class DiscoveryResult(BaseModel):
    """Output of the daily discovery LLM node."""
    subreddits: List[DiscoveryItem] = Field(
        default_factory=list,
        description="Up to 10 suggested new subreddits.",
    )
    search_queries: List[DiscoveryItem] = Field(
        default_factory=list,
        description="Up to 10 suggested search queries with listing modes.",
    )
    explanation: str = Field(
        ...,
        description="Summary of why these sources match current audience comedy preferences.",
    )


class TitlePrescreenDecision(BaseModel):
    """Decision on whether a single video candidate passes the text-only pre-screen."""
    post_id: str = Field(..., description="The candidate's unique post_id")
    keep: bool = Field(..., description="True to keep and download; False to drop")
    drop_reason: Optional[str] = Field(
        default=None,
        description="If dropped, reason (e.g. 'news_politics', 'discussion_qna', 'tv_interview', 'text_meme')",
    )


class TitlePrescreenResult(BaseModel):
    """Batch output of the cautious title pre-screen LLM."""
    decisions: List[TitlePrescreenDecision] = Field(default_factory=list)


class BorderlineRankItem(BaseModel):
    """Comparative ranking for a borderline clip."""
    post_id: str = Field(..., description="The video's post_id")
    rank: int = Field(..., ge=1, description="Rank position (1 = best/funniest)")
    calibrated_score: float = Field(..., ge=1.0, le=10.0, description="Calibrated score on 1-10 rubric")
    comparative_reason: str = Field(..., description="Head-to-head comparison explaining rank")


class BorderlineRankResult(BaseModel):
    """Output of comparative side-by-side ranking for borderline clips."""
    ranked_clips: List[BorderlineRankItem] = Field(default_factory=list)


