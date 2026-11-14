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


def test_pre_download_duration_filter_with_valid_duration():
    cand = VideoCandidate(
        source="reddit",
        post_id="p1",
        post_url="https://reddit.com/p1",
        title="Funny cat jumping",
        uploader="user1",
        score=500,
        duration=45.0,
    )
    passed, reason = check_candidate_pre_download(cand, CONFIG)
    assert passed


def test_pre_download_duration_filter_too_long():
    cand = VideoCandidate(
        source="reddit",
        post_id="p_long",
        post_url="https://reddit.com/p_long",
        title="Long interview video",
        uploader="user1",
        score=500,
        duration=120.0,
    )
    passed, reason = check_candidate_pre_download(cand, CONFIG)
    assert not passed
    assert "duration_too_long" in reason
    assert "120.0s" in reason


def test_pre_download_duration_filter_too_short():
    cand = VideoCandidate(
        source="reddit",
        post_id="p_short",
        post_url="https://reddit.com/p_short",
        title="Too short meme",
        uploader="user1",
        score=500,
        duration=3.0,
    )
    passed, reason = check_candidate_pre_download(cand, CONFIG)
    assert not passed
    assert "duration_too_short" in reason
    assert "3.0s" in reason


def test_pre_download_duration_filter_hard_cap_180s():
    # Even if max_duration is set to 250s, hard cap 180s must reject it
    lenient_config = {"filters": {"max_duration_seconds": 250.0, "min_duration_seconds": 5.0}}
    cand = VideoCandidate(
        source="reddit",
        post_id="p_huge",
        post_url="https://reddit.com/p_huge",
        title="Way too long clip",
        uploader="user1",
        score=500,
        duration=195.0,
    )
    passed, reason = check_candidate_pre_download(cand, lenient_config)
    assert not passed
    assert "duration_too_long" in reason


def test_pre_download_candidate_without_duration_passes_for_probe():
    # RSS candidates have duration=None; should pass pre-download to be probed before download
    cand = VideoCandidate(
        source="reddit",
        post_id="p_rss",
        post_url="https://reddit.com/p_rss",
        title="Funny cat jumping around the room",
        uploader="user1",
        score=500,
        duration=None,
    )
    passed, reason = check_candidate_pre_download(cand, CONFIG)
    assert passed


def test_downloader_remote_probe_fallback():
    from unittest.mock import patch, MagicMock
    import tempfile
    from pathlib import Path
    from core.downloader import VideoDownloader
    from core.db import VideoDatabase
    from core.dedupe import Deduplicator

    with tempfile.TemporaryDirectory() as tmp_dir:
        db = VideoDatabase(str(Path(tmp_dir) / "v.db"))
        dedup = Deduplicator(db)
        downloader = VideoDownloader(db, dedup, config={"filters": {"min_duration_seconds": 5.0, "max_duration_seconds": 90.0}})

        # 1. Candidate with unknown duration probed as 120s -> dropped before downloading
        cand_long = VideoCandidate(
            source="reddit",
            post_id="probe_long",
            post_url="https://reddit.com/r/funny/comments/abc/long",
            title="Probed Long Video",
            uploader="u1",
            duration=None,
        )
        dest_long = Path(tmp_dir) / "probe_long"
        with patch("core.downloader.probe_remote_duration", return_value=120.0):
            success, msg, meta = downloader.process_candidate(cand_long, dest_long)
            assert not success
            assert "duration_too_long" in msg
            assert "(probed)" in msg
            assert not dest_long.exists()

        # 2. Candidate with unknown duration probed as 2.0s -> dropped as too short
        cand_short = VideoCandidate(
            source="reddit",
            post_id="probe_short",
            post_url="https://reddit.com/r/funny/comments/def/short",
            title="Probed Short Video",
            uploader="u2",
            duration=None,
        )
        dest_short = Path(tmp_dir) / "probe_short"
        with patch("core.downloader.probe_remote_duration", return_value=2.0):
            success, msg, meta = downloader.process_candidate(cand_short, dest_short)
            assert not success
            assert "duration_too_short" in msg
            assert "(probed)" in msg
            assert not dest_short.exists()


def test_probe_hls_duration_http_direct_media_playlist():
    from unittest.mock import patch, MagicMock
    from core.downloader import probe_hls_duration_http

    media_playlist_text = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:5
#EXTINF:4.000,
segment0.ts
#EXTINF:4.500,
segment1.ts
#EXTINF:2.500,
segment2.ts
#EXT-X-ENDLIST"""

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = media_playlist_text

    with patch("requests.get", return_value=mock_resp):
        dur = probe_hls_duration_http("https://v.redd.it/dummy/media.m3u8")
        assert dur == 11.0


def test_probe_hls_duration_http_master_playlist_variants():
    from unittest.mock import patch, MagicMock
    from core.downloader import probe_hls_duration_http

    master_text = """#EXTM3U
#EXT-X-VERSION:4
#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360
CMAF_360.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=1500000,RESOLUTION=1280x720
CMAF_720.m3u8"""

    variant_text = """#EXTM3U
#EXT-X-TARGETDURATION:4
#EXTINF:3.800,
CMAF_360_0.m4s
#EXTINF:3.800,
CMAF_360_1.m4s
#EXTINF:1.500,
CMAF_360_2.m4s
#EXT-X-ENDLIST"""

    def mock_get(url, *args, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        if "HLSPlaylist.m3u8" in url:
            resp.text = master_text
        else:
            resp.text = variant_text
        return resp

    with patch("requests.get", side_effect=mock_get):
        dur = probe_hls_duration_http("https://v.redd.it/abc1234/HLSPlaylist.m3u8")
        assert dur == 9.1


def test_probe_remote_duration_reddit_hls_integration():
    from unittest.mock import patch, MagicMock
    from core.downloader import probe_remote_duration

    master_text = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=1200000,RESOLUTION=720x1280
CMAF_720.m3u8"""

    # 112.93s video like the real Reddit test clip
    segments = "\n".join([f"#EXTINF:4.000,\nseg_{i}.ts" for i in range(28)]) + "\n#EXTINF:0.933,\nseg_last.ts\n#EXT-X-ENDLIST"
    variant_text = f"#EXTM3U\n#EXT-X-TARGETDURATION:4\n{segments}"

    def mock_get(url, *args, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        if "HLSPlaylist.m3u8" in url:
            resp.text = master_text
        else:
            resp.text = variant_text
        return resp

    with patch("requests.get", side_effect=mock_get):
        # Pass a bare v.redd.it URL - probe_remote_duration should resolve to HLSPlaylist.m3u8
        dur = probe_remote_duration("https://v.redd.it/336ynt6hri1i1")
        assert dur == 112.93


