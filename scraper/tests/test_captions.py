import pytest
from core.captions import clean_caption


def test_clean_caption_removes_urls():
    raw = "Check this out https://reddit.com/r/funny/12345 hilarious dog"
    cleaned = clean_caption(raw, max_words=8)
    assert "https://" not in cleaned
    assert "Check this out" in cleaned


def test_clean_caption_removes_reddit_tags():
    raw = "[OC] Cat tries to jump on the table (gone wrong) [Wait for end]"
    cleaned = clean_caption(raw, max_words=8)
    assert "[OC]" not in cleaned
    assert "Cat tries to jump" in cleaned


def test_clean_caption_word_limit():
    raw = "A very long descriptive title about a dog that was running in the park and jumped over three huge fences"
    cleaned = clean_caption(raw, max_words=6)
    # Exclude emojis when splitting words
    words = [w for w in cleaned.split() if not any(ord(c) > 0xFFFF for c in w)]
    assert len(words) <= 6


def test_clean_caption_strips_mentions_and_hashtags():
    raw = "Look at this @username #funny #viral dog"
    cleaned = clean_caption(raw, max_words=8)
    assert "@" not in cleaned
    assert "#" not in cleaned
    assert "Look at this dog" in cleaned


def test_clean_caption_preserves_emoji():
    raw = "He really thought he had it 😂"
    cleaned = clean_caption(raw, max_words=8, preserve_emojis=True)
    assert "😂" in cleaned

