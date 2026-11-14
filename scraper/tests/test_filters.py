from core.filters import check_candidate_pre_download, check_media_post_download
from sources.base import VideoCandidate

CONFIG = {
    "filters": {
        "min_duration": 5.0,
        "max_duration": 60.0,
        "min_height": 480,
        "min_width": 480,
        "skip_nsfw": True,
        "enforce_english": True,
        "min_upvotes": 50,
        "keyword_blocklist": ["death", "kill", "gore", "politics", "war"],
    }
}


def test_nsfw_filter():
    cand = VideoCandidate(
        source="reddit",
        post_id="test_nsfw",
        post_url="https://reddit.com/test",
        title="Funny clip",
        uploader="user1",
        score=500,
        is_nsfw=True,
    )
    passed, reason = check_candidate_pre_download(cand, CONFIG)
    assert not passed
    assert "NSFW" in reason


def test_keyword_blocklist_filter():
    cand = VideoCandidate(
        source="reddit",
        post_id="test_kw",
        post_url="https://reddit.com/test",
        title="Tragic death in war zone",
        uploader="user1",
        score=500,
        is_nsfw=False,
    )
    passed, reason = check_candidate_pre_download(cand, CONFIG)
    assert not passed
    assert "Blocked keyword" in reason


def test_keyword_blocklist_stem_and_suffixes():
    config_with_injur = {
        "filters": {
            "keyword_blocklist": ["kill", "injur"],
            "min_upvotes": 0,
        }
    }
    # "kill" should match "killed" and "killing"
    cand_killed = VideoCandidate(
        source="reddit", post_id="k1", post_url="https://reddit.com/1",
        title="Cat killed a spider", uploader="user1", score=500,
    )
    passed, reason = check_candidate_pre_download(cand_killed, config_with_injur)
    assert not passed
    assert "Blocked keyword: 'kill'" in reason

    # "injur" should match "injured" and "injuries"
    cand_injured = VideoCandidate(
        source="reddit", post_id="i1", post_url="https://reddit.com/2",
        title="Skateboarder injured after failed trick", uploader="user1", score=500,
    )
    passed, reason = check_candidate_pre_download(cand_injured, config_with_injur)
    assert not passed
    assert "Blocked keyword: 'injur'" in reason


def test_valid_candidate_passes():
    cand = VideoCandidate(
        source="reddit",
        post_id="test_valid",
        post_url="https://reddit.com/test",
        title="Dog slips on ice trying to catch ball",
        uploader="user1",
        score=350,
        is_nsfw=False,
    )
    passed, reason = check_candidate_pre_download(cand, CONFIG)
    assert passed


def test_media_duration_filter():
    # Too short
    passed, reason = check_media_post_download(2.5, 1080, 1920, CONFIG)
    assert not passed
    assert "too short" in reason

    # Too long
    passed, reason = check_media_post_download(85.0, 1080, 1920, CONFIG)
    assert not passed
    assert "too long" in reason

    # Just right
    passed, reason = check_media_post_download(22.0, 1080, 1920, CONFIG)
    assert passed


def test_media_resolution_filter():
    # Low resolution
    passed, reason = check_media_post_download(15.0, 320, 240, CONFIG)
    assert not passed
    assert "Resolution too low" in reason

    # Good resolution
    passed, reason = check_media_post_download(15.0, 720, 1280, CONFIG)
    assert passed

