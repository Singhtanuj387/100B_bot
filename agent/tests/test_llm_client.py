import os
import pytest
from unittest.mock import patch, MagicMock
from llm.client import LLMClient


@pytest.fixture
def sample_config():
    return {
        "models": {
            "judge": "claude-haiku-5-5",
            "caption": "claude-haiku-5-5",
            "plan": "claude-sonnet-5-5",
        },
        "cost_limits": {
            "max_cost_per_run_usd": 2.00,
            "max_llm_calls_per_run": 100,
        },
    }


def test_live_run_without_api_key_raises_error(sample_config):
    with patch.dict(os.environ, {}, clear=True):
        if "ANTHROPIC_API_KEY" in os.environ:
            del os.environ["ANTHROPIC_API_KEY"]
        with pytest.raises(ValueError, match="ANTHROPIC_API_KEY is missing"):
            LLMClient(config=sample_config, dry_run=False)


def test_dry_run_without_api_key_succeeds_in_mock_mode(sample_config):
    with patch.dict(os.environ, {}, clear=True):
        if "ANTHROPIC_API_KEY" in os.environ:
            del os.environ["ANTHROPIC_API_KEY"]
        client = LLMClient(config=sample_config, dry_run=True)
        assert client.dry_run is True
        assert client.client is None


def test_live_run_with_api_key_initializes(sample_config):
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test-key-12345"}):
        with patch("anthropic.Anthropic") as mock_anthropic:
            client = LLMClient(config=sample_config, dry_run=False)
            assert client.dry_run is False
            assert client.api_key == "sk-ant-test-key-12345"
            mock_anthropic.assert_called_once_with(api_key="sk-ant-test-key-12345", timeout=75.0)


def test_pricing_calculation_haiku_and_sonnet(sample_config):
    client = LLMClient(config=sample_config, dry_run=True)
    # claude-haiku-5-5: $0.10 input / $0.50 output per 1M tokens
    cost_haiku = client._calculate_cost("claude-haiku-5-5", in_tokens=1_000_000, out_tokens=1_000_000)
    assert pytest.approx(cost_haiku, rel=1e-4) == 0.60

    # claude-sonnet-5-5: $2.00 input / $10.00 output per 1M tokens
    cost_sonnet = client._calculate_cost("claude-sonnet-5-5", in_tokens=1_000_000, out_tokens=1_000_000)
    assert pytest.approx(cost_sonnet, rel=1e-4) == 12.00


def test_google_genai_initialization(sample_config):
    from pydantic import BaseModel

    class DummySchema(BaseModel):
        msg: str

    with patch.dict(os.environ, {"GEMINI_API_KEY": "AIzaSyTestKey123"}):
        with patch("google.genai.Client") as mock_genai_client:
            mock_inst = MagicMock()
            mock_genai_client.return_value = mock_inst

            client = LLMClient(config=sample_config, dry_run=False)
            assert client.provider == "google"
            assert client.api_key == "AIzaSyTestKey123"
            mock_genai_client.assert_called_once()
            call_kwargs = mock_genai_client.call_args[1]
            assert call_kwargs["api_key"] == "AIzaSyTestKey123"
            assert call_kwargs["http_options"].timeout == 75000

            # Test structured call
            mock_resp = MagicMock()
            mock_resp.text = '{"msg": "Hello Google GenAI"}'
            mock_resp.usage_metadata = MagicMock(prompt_token_count=10, candidates_token_count=5)
            mock_inst.models.generate_content.return_value = mock_resp

            result = client.call_structured(
                task_type="judge",
                system_prompt="Test sys",
                user_prompt="Test user",
                schema=DummySchema,
            )
            assert result.msg == "Hello Google GenAI"
            assert client.call_count == 1
            assert client.total_cost_usd == 0.0


def test_per_call_timeout_triggers():
    from pydantic import BaseModel
    import time

    class DummySchema(BaseModel):
        msg: str

    timeout_config = {
        "models": {"judge": "gemma-4-31b-it", "judge_fallback": "gemini-3.1-flash-lite"},
        "cost_limits": {"request_timeout_seconds": 0.2, "max_cost_per_run_usd": 2.0},
    }

    with patch.dict(os.environ, {"GEMINI_API_KEY": "AIzaSyTestKey123"}):
        with patch("google.genai.Client") as mock_genai_client:
            mock_inst = MagicMock()
            mock_genai_client.return_value = mock_inst

            # Simulate a hanging call that sleeps longer than timeout
            def slow_generate(*args, **kwargs):
                time.sleep(1.0)
                mock_resp = MagicMock()
                mock_resp.text = '{"msg": "slow"}'
                return mock_resp

            mock_inst.models.generate_content.side_effect = slow_generate

            client = LLMClient(config=timeout_config, dry_run=False)
            assert client.request_timeout == 0.2

            with pytest.raises(RuntimeError, match="Google GenAI call failed"):
                client.call_structured(
                    task_type="judge",
                    system_prompt="Test",
                    user_prompt="Test",
                    schema=DummySchema,
                    max_retries=0,
                )


def test_gemma_fallback_to_gemini_3_1_flash_lite():
    """Verify that when Gemma encounters an error, it falls back to gemini-3.1-flash-lite."""
    from pydantic import BaseModel

    class DummySchema(BaseModel):
        score: float

    cfg = {
        "models": {
            "judge": "gemma-4-31b-it",
            "judge_fallback": "gemini-3.1-flash-lite",
        },
        "cost_limits": {"request_timeout_seconds": 10.0, "max_cost_per_run_usd": 2.0},
    }

    with patch.dict(os.environ, {"GEMINI_API_KEY": "AIzaSyTestKey123"}):
        with patch("google.genai.Client") as mock_genai_client:
            mock_inst = MagicMock()
            mock_genai_client.return_value = mock_inst

            models_called = []

            def mock_generate(model, contents, config):
                models_called.append(model)
                if model == "gemma-4-31b-it":
                    raise RuntimeError("503 Service Unavailable: Gemma overloaded")
                mock_resp = MagicMock()
                mock_resp.text = '{"score": 8.5}'
                mock_resp.usage_metadata.prompt_token_count = 100
                mock_resp.usage_metadata.candidates_token_count = 20
                return mock_resp

            mock_inst.models.generate_content.side_effect = mock_generate

            client = LLMClient(config=cfg, dry_run=False)
            res = client.call_structured(
                task_type="judge",
                system_prompt="sys",
                user_prompt="usr",
                schema=DummySchema,
                max_retries=0,
            )

            assert res.score == 8.5
            # Must have attempted Gemma first, then fallen back to gemini-3.1-flash-lite
            assert "gemma-4-31b-it" in models_called
            assert "gemini-3.1-flash-lite" in models_called
            assert models_called.index("gemma-4-31b-it") < models_called.index("gemini-3.1-flash-lite")


def test_per_model_rate_limits_separate_windows():
    """Verify that Gemma and Gemini Flash Lite have distinct rate limiters and token limits."""
    cfg = {
        "rate_limits": {
            "safety_buffer": 0.85,
            "models": {
                "gemma-4-31b-it": {
                    "max_requests_per_minute": 30,
                    "max_input_tokens_per_minute": 16000,
                    "max_requests_per_day": 14400,
                },
                "gemini-3.1-flash-lite": {
                    "max_requests_per_minute": 15,
                    "max_input_tokens_per_minute": 250000,
                    "max_requests_per_day": 500,
                },
                "default": {
                    "max_requests_per_minute": 20,
                    "max_input_tokens_per_minute": 30000,
                    "max_requests_per_day": 1000,
                },
            },
        },
        "models": {
            "judge": "gemma-4-31b-it",
            "caption": "gemini-3.1-flash-lite",
        },
    }

    client = LLMClient(config=cfg, dry_run=True)

    gemma_limiter = client.get_rate_limiter("gemma-4-31b-it")
    flash_limiter = client.get_rate_limiter("gemini-3.1-flash-lite")
    default_limiter = client.get_rate_limiter("some-other-model")

    # Limiters must be distinct objects
    assert gemma_limiter is not flash_limiter
    assert gemma_limiter is not default_limiter

    # TPM check: Gemma is capped at 16k * 0.85 = 13,600 TPM
    assert gemma_limiter.max_tpm == int(16000 * 0.85)
    assert gemma_limiter.max_rpm == int(30 * 0.85)
    assert gemma_limiter.max_rpd == 14400

    # TPM check: Flash Lite has 250,000 * 0.85 = 212,500 TPM
    assert flash_limiter.max_tpm == int(250000 * 0.85)
    assert flash_limiter.max_rpm == int(15 * 0.85)
    assert flash_limiter.max_rpd == 500

    # Default limiter check
    assert default_limiter.max_tpm == int(30000 * 0.85)
    assert default_limiter.max_rpm == int(20 * 0.85)

    # Activity on Gemma must NOT consume Flash Lite token window
    gemma_limiter.record_call(input_tokens=10000)
    assert len(gemma_limiter.request_times) == 1
    assert len(flash_limiter.request_times) == 0
    assert sum(tok for (_, tok) in flash_limiter.token_window) == 0



