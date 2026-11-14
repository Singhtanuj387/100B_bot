from graph.nodes.caption import clean_caption_text


def test_clean_caption_word_limit():
    long_caption = "This is a super long funny video caption that clearly exceeds eight words completely"
    cleaned = clean_caption_text(long_caption)
    words = cleaned.split()
    assert len(words) <= 8


def test_clean_caption_single_emoji_retention():
    multi_emoji = "He really thought he had it 😂🤣💀"
    cleaned = clean_caption_text(multi_emoji)
    words = cleaned.split()
    assert len(words) <= 8
    # Should only retain 1 emoji
    assert "😂" in cleaned
    assert "🤣" not in cleaned
    assert "💀" not in cleaned


def test_clean_caption_strip_hashtags_and_urls():
    text_with_tags = "Watch this crazy moment #viral #shorts https://t.co/xyz123 @someuser 😂"
    cleaned = clean_caption_text(text_with_tags)
    assert "#" not in cleaned
    assert "http" not in cleaned
    assert "@" not in cleaned
    assert len(cleaned.split()) <= 8


def test_clean_caption_strip_reddit_tags():
    reddit_title = "[OC] Guy slips on ice (watch till end)"
    cleaned = clean_caption_text(reddit_title)
    assert "[OC]" not in cleaned
    assert "(watch till end)" not in cleaned
    assert "Guy slips on ice" in cleaned


def test_caption_node_skips_already_edited():
    from unittest.mock import MagicMock
    from graph.nodes.caption import caption_node

    mock_llm = MagicMock()
    mock_llm.total_cost_usd = 0.0
    mock_llm.call_count = 0

    state = {
        "approved": [
            {
                "post_id": "res_vid",
                "already_edited": True,
                "caption": "Pre-made caption 😂",
                "youtube_title": "Pre-made title",
                "youtube_description": "Pre-made desc",
            }
        ]
    }
    result = caption_node(state, mock_llm)
    # LLM should not be called
    mock_llm.call_structured.assert_not_called()
    assert result["approved"][0]["caption"] == "Pre-made caption 😂"


def test_caption_node_generates_llm_caption_when_approved_without_editing():
    from unittest.mock import MagicMock
    from graph.nodes.caption import caption_node
    from llm.schemas import CaptionResult

    mock_llm = MagicMock()
    mock_llm.call_structured.return_value = CaptionResult(
        caption="LLM generated caption 😂",
        youtube_title="LLM Title #shorts",
        youtube_description="LLM Description",
    )

    state = {
        "approved": [
            {
                "post_id": "vid_approved_a",
                "title": "Original Post Title",
                "caption": "Scraper rule caption 😂",  # Scraper rule-based caption
                "human_decision": "approved",
                "caption_edited_by_human": False,     # User pressed 'A', didn't edit caption
            }
        ]
    }
    result = caption_node(state, mock_llm)
    # LLM must be called
    mock_llm.call_structured.assert_called_once()
    assert result["approved"][0]["caption"] == "LLM generated caption 😂"
    assert result["approved"][0]["youtube_title"] == "LLM Title #shorts"


def test_caption_node_skips_llm_when_caption_edited_by_human():
    from unittest.mock import MagicMock
    from graph.nodes.caption import caption_node

    mock_llm = MagicMock()

    state = {
        "approved": [
            {
                "post_id": "vid_edited_e",
                "title": "Original Post Title",
                "caption": "Manually entered punchline 🐱",
                "human_decision": "approved",
                "caption_edited_by_human": True,      # User explicitly pressed 'E'
            }
        ]
    }
    result = caption_node(state, mock_llm)
    # LLM must be skipped
    mock_llm.call_structured.assert_not_called()
    assert result["approved"][0]["caption"] == "Manually entered punchline 🐱"


def test_is_generic_title():
    from graph.nodes.caption import is_generic_title

    assert is_generic_title("Wait till you see what happens next! 😂 #shorts #funny") is True
    assert is_generic_title("Wait until you see what happens next") is True
    assert is_generic_title("Wait for the end 😂") is True
    assert is_generic_title("Funny moment") is True
    assert is_generic_title("") is True
    assert is_generic_title(None) is True
    assert is_generic_title("Chicken Paws Locked and Loaded #shorts #funny") is False
    assert is_generic_title("He Had No Idea It Was A Paper Wasp Nest") is False


def test_caption_node_refines_generic_title_for_reserve_clip():
    from unittest.mock import MagicMock
    from graph.nodes.caption import caption_node

    mock_llm = MagicMock()
    mock_llm.total_cost_usd = 0.0
    mock_llm.call_count = 0

    state = {
        "approved": [
            {
                "post_id": "res_generic",
                "already_edited": True,
                "title": "Dog tries to catch soap bubble",
                "caption": "Soap bubble hunt 😂",
                "youtube_title": "Wait till you see what happens next! 😂 #shorts #funny",  # Generic title!
                "youtube_description": "Pre-made desc",
            }
        ]
    }
    result = caption_node(state, mock_llm)
    refined_title = result["approved"][0]["youtube_title"]
    # Generic title must be replaced by content-specific title
    assert "Wait till you see what happens next" not in refined_title
    assert "Dog tries to catch soap bubble" in refined_title


def test_caption_node_disambiguates_duplicate_titles():
    from unittest.mock import MagicMock
    from graph.nodes.caption import caption_node
    from llm.schemas import CaptionResult

    mock_llm = MagicMock()
    mock_llm.total_cost_usd = 0.0
    mock_llm.call_count = 0
    # Simulate LLM returning identical title for two different clips
    mock_llm.call_structured.return_value = CaptionResult(
        caption="Same punchline 😂",
        youtube_title="Identical Video Title #shorts #funny",
        youtube_description="Desc",
    )

    state = {
        "approved": [
            {
                "post_id": "clip_1",
                "title": "First distinct incident",
                "caption": "Punchline 1",
            },
            {
                "post_id": "clip_2",
                "title": "Second distinct incident",
                "caption": "Punchline 2",
            },
        ]
    }
    result = caption_node(state, mock_llm)
    t1 = result["approved"][0]["youtube_title"]
    t2 = result["approved"][1]["youtube_title"]
    assert t1 != t2
    assert "Identical Video Title" in t1




