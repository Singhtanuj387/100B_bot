import os
import re
import json
import time
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import List, Dict, Any, Optional, Type, TypeVar
from pydantic import BaseModel
from dotenv import load_dotenv
from loguru import logger

from llm.schemas import JudgeResult, CaptionResult, PlanResult

# Load .env if present in current dir, agent dir, or workspace root
for env_candidate in [Path.cwd() / ".env", Path(__file__).parents[1] / ".env", Path(__file__).parents[2] / ".env"]:
    if env_candidate.is_file():
        load_dotenv(dotenv_path=env_candidate)
load_dotenv()

T = TypeVar("T", bound=BaseModel)

# Approximate pricing per 1M tokens in USD
MODEL_PRICING = {
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50},
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00},
    "claude-3-5-haiku": {"input": 0.80, "output": 4.00},
    "claude-3-haiku": {"input": 0.25, "output": 1.25},
    "claude-3-5-sonnet": {"input": 3.00, "output": 15.00},
    "default": {"input": 1.00, "output": 5.00},
}


class SlidingWindowRateLimiter:
    """
    Precision Sliding-Window Rate Limiter enforcing:
      - Peak Requests Per Minute (RPM)
      - Peak Input Tokens Per Minute (TPM)
      - Peak Requests Per Day (RPD)
    Automatically pauses requests to prevent 429 quota exhaustion.
    """

    def __init__(
        self,
        max_rpm: int = 30,
        max_tpm: int = 16000,
        max_rpd: int = 14400,
        safety_buffer: float = 0.85,
        model_name: str = "default",
    ):
        self.model_name = model_name
        self.max_rpm = max(1, int(max_rpm * safety_buffer))          # e.g. 25 RPM safe target
        self.max_tpm = max(500, int(max_tpm * safety_buffer))        # e.g. 13,600 TPM safe target
        self.max_rpd = int(max_rpd)
        self.request_times: List[float] = []
        self.token_window: List[tuple] = []                           # (timestamp, tokens)
        self.daily_count = 0
        self.daily_reset_time = time.time() + 86400.0

    def wait_if_needed(self, estimated_input_tokens: int = 500) -> None:
        now = time.time()

        # Reset daily counter every 24 hours
        if now > self.daily_reset_time:
            self.daily_count = 0
            self.daily_reset_time = now + 86400.0

        if self.daily_count >= self.max_rpd:
            raise RuntimeError(
                f"Daily LLM request quota reached for {self.model_name}: {self.daily_count}/{self.max_rpd} RPD. "
                "Halting to protect account limits."
            )

        # 1. Purge events older than 60s
        self.request_times = [t for t in self.request_times if now - t < 60.0]
        self.token_window = [(t, tok) for (t, tok) in self.token_window if now - t < 60.0]

        # 2. Check RPM limit
        while len(self.request_times) >= self.max_rpm:
            oldest = self.request_times[0]
            sleep_sec = max(0.2, 60.0 - (time.time() - oldest) + 0.2)
            logger.info(
                f"Rate limiter pacing ({self.model_name}): {len(self.request_times)}/{self.max_rpm} RPM reached. "
                f"Pausing {sleep_sec:.1f}s to respect RPM limit..."
            )
            time.sleep(sleep_sec)
            now = time.time()
            self.request_times = [t for t in self.request_times if now - t < 60.0]

        # 3. Check TPM limit
        current_tokens = sum(tok for (_, tok) in self.token_window)
        while (current_tokens + estimated_input_tokens) > self.max_tpm:
            if not self.token_window:
                break
            oldest_t, _ = self.token_window[0]
            sleep_sec = max(0.2, 60.0 - (time.time() - oldest_t) + 0.2)
            logger.info(
                f"Rate limiter pacing ({self.model_name}): input tokens ({current_tokens} + {estimated_input_tokens}) > {self.max_tpm} TPM limit. "
                f"Pausing {sleep_sec:.1f}s to respect TPM limit..."
            )
            time.sleep(sleep_sec)
            now = time.time()
            self.token_window = [(t, tok) for (t, tok) in self.token_window if now - t < 60.0]
            current_tokens = sum(tok for (_, tok) in self.token_window)

        # 4. Enforce minimum inter-request spacing
        if self.request_times:
            min_interval = 60.0 / self.max_rpm
            elapsed = time.time() - self.request_times[-1]
            if elapsed < min_interval:
                time.sleep(min_interval - elapsed)

    def record_call(self, input_tokens: int) -> None:
        now = time.time()
        self.request_times.append(now)
        self.token_window.append((now, input_tokens))
        self.daily_count += 1


class LLMClient:
    """
    Unified LLM Client interfacing with Anthropic Claude API.
    Features:
      - Vision support with base64 image encoding
      - Structured output parsing with schema validation and auto-retry
      - Token usage and dollar cost tracking
      - Budget and call limits enforcement
      - Intelligent Mock mode for --dry-run and cost-free offline testing
    """

    def __init__(
        self,
        config: Dict[str, Any],
        dry_run: bool = False,
        db_handler: Optional[Any] = None,
    ):
        self.config = config
        self.dry_run = dry_run
        self.db = db_handler

        anthropic_raw = os.getenv("ANTHROPIC_API_KEY") or ""
        self.anthropic_key = anthropic_raw.strip('"\' ') or None

        gemini_raw = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or os.getenv("GOOGLE_GENAI_API_KEY") or ""
        self.gemini_key = gemini_raw.strip('"\' ') or None

        self.provider = "mock"
        self.api_key = None

        self.models_config = config.get("models", {})
        self.cost_limits = config.get("cost_limits", {})
        self.max_cost = float(self.cost_limits.get("max_cost_per_run_usd", self.cost_limits.get("max_cost_per_day_usd", 2.00)))
        self.max_calls = int(self.cost_limits.get("max_llm_calls_per_run", 100))
        # Per-call timeout for every LLM request (about 60 to 90 seconds, default: 75.0s)
        self.request_timeout = float(self.cost_limits.get("request_timeout_seconds", 75.0))

        self.total_cost_usd = 0.0
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.call_count = 0

        # Per-Model Rate Limiters Registry
        self.rate_limiters: Dict[str, SlidingWindowRateLimiter] = {}

        self.client = None
        if not self.dry_run:
            configured_models_str = " ".join(str(v) for v in self.models_config.values()).lower()
            if "claude" in configured_models_str and self.anthropic_key:
                self.provider = "anthropic"
                self.api_key = self.anthropic_key
            elif ("gemini" in configured_models_str or "gemma" in configured_models_str) and self.gemini_key:
                self.provider = "google"
                self.api_key = self.gemini_key
            elif self.gemini_key:
                self.provider = "google"
                self.api_key = self.gemini_key
            elif self.anthropic_key:
                self.provider = "anthropic"
                self.api_key = self.anthropic_key
            else:
                raise ValueError(
                    "ANTHROPIC_API_KEY is missing or GEMINI_API_KEY is missing! A real curation run requires a valid GEMINI_API_KEY or ANTHROPIC_API_KEY in .env.\n"
                    "To simulate execution without calling the paid API, run with --dry-run."
                )

        if not self.dry_run:
            if self.provider == "google":
                try:
                    from google import genai
                    from google.genai import types
                    http_opts = types.HttpOptions(
                        timeout=int(self.request_timeout * 1000),
                        retry_options=types.HttpRetryOptions(attempts=1),
                    )
                    self.client = genai.Client(api_key=self.gemini_key, http_options=http_opts)
                    logger.info(f"Initialized Google GenAI (Gemini/Gemma) client with {self.request_timeout:.0f}s timeout.")
                except Exception as e:
                    logger.error(f"Failed to initialize Google GenAI client: {e}")
                    raise
            elif self.provider == "anthropic":
                try:
                    import anthropic
                    self.client = anthropic.Anthropic(api_key=self.anthropic_key, timeout=self.request_timeout)
                    logger.info(f"Initialized Anthropic Claude client with {self.request_timeout:.0f}s timeout.")
                except Exception as e:
                    logger.error(f"Failed to initialize Anthropic client with provided API key: {e}")
                    raise
        else:
            logger.info("Dry-run active. Running in Mock LLM Mode (zero API cost).")

    def _get_model_name(self, task_type: str) -> str:
        """Fetch model name from config based on task type (judge, caption, plan)."""
        configured = self.models_config.get(task_type, "")
        if self.provider == "google":
            if not configured or "claude" in configured.lower():
                return "gemma-4-31b-it"
            return configured
        return configured or "claude-haiku-5-5"

    def get_rate_limiter(self, model_name: str) -> SlidingWindowRateLimiter:
        """
        Retrieve or instantiate a dedicated rate limiter for the given model.
        Reads per-model limits from config['rate_limits']['models'].
        Ensures text models (e.g. gemini-3.1-flash-lite at 250k TPM) are never
        throttled by vision models (e.g. gemma-4-31b-it at 16k TPM).
        """
        clean_model = (model_name or "default").strip()
        if clean_model in self.rate_limiters:
            return self.rate_limiters[clean_model]

        rl_cfg = self.config.get("rate_limits", {})
        models_table = rl_cfg.get("models", {})
        safety_buffer = float(rl_cfg.get("safety_buffer", 0.85))

        model_limits = None
        # Match exact key or substring match (case-insensitive)
        for key, limits in models_table.items():
            if key.lower() == clean_model.lower() or key.lower() in clean_model.lower() or clean_model.lower() in key.lower():
                model_limits = limits
                break

        if not model_limits:
            model_limits = models_table.get("default", {})
        if not model_limits:
            model_limits = rl_cfg

        rpm = int(model_limits.get("max_requests_per_minute") or rl_cfg.get("max_requests_per_minute") or 30)
        tpm = int(model_limits.get("max_input_tokens_per_minute") or rl_cfg.get("max_input_tokens_per_minute") or 30000)
        rpd = int(model_limits.get("max_requests_per_day") or rl_cfg.get("max_requests_per_day") or 1000)

        limiter = SlidingWindowRateLimiter(
            max_rpm=rpm,
            max_tpm=tpm,
            max_rpd=rpd,
            safety_buffer=safety_buffer,
            model_name=clean_model,
        )
        self.rate_limiters[clean_model] = limiter
        return limiter

    @property
    def rate_limiter(self) -> SlidingWindowRateLimiter:
        """Backwards compatibility property returning the default/active rate limiter."""
        primary_model = self._get_model_name("judge")
        return self.get_rate_limiter(primary_model)

    def _check_limits(self) -> None:
        """Enforce cost and call safety bounds."""
        if self.call_count >= self.max_calls:
            raise RuntimeError(
                f"LLM Call Limit reached: {self.call_count}/{self.max_calls} calls. "
                "Stopping execution to protect budget."
            )
        if self.total_cost_usd >= self.max_cost:
            raise RuntimeError(
                f"LLM Budget Cap reached: ${self.total_cost_usd:.4f} >= ${self.max_cost:.2f}. "
                "Stopping execution to protect budget."
            )

    def _calculate_cost(self, model_name: str, in_tokens: int, out_tokens: int) -> float:
        """Compute estimated USD cost for a given call."""
        pricing_key = "default"
        for key in MODEL_PRICING:
            if key in model_name.lower():
                pricing_key = key
                break
        rates = MODEL_PRICING[pricing_key]
        cost = (in_tokens / 1_000_000.0) * rates["input"] + (out_tokens / 1_000_000.0) * rates["output"]
        return cost

    def _extract_json(self, text: str) -> str:
        """Extract clean JSON string from LLM markdown code blocks or text."""
        text = text.strip()
        match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
        if match:
            return match.group(1).strip()
        return text

    def call_structured(
        self,
        task_type: str,
        system_prompt: str,
        user_prompt: str,
        schema: Type[T],
        images_base64: Optional[List[str]] = None,
        max_retries: int = 2,
    ) -> T:
        """Call Claude with optional vision and return a validated Pydantic model."""
        self._check_limits()
        model_name = self._get_model_name(task_type)

        # Handle Mock Mode
        if self.client is None or self.dry_run or self.provider == "mock":
            return self._generate_mock_output(task_type, user_prompt, schema)

        # ----------------------------------------------------------------------
        # Google GenAI Provider (Gemini / Gemma via Google AI Studio)
        # ----------------------------------------------------------------------
        if self.provider == "google":
            from google.genai import types
            import base64

            contents: List[Any] = []
            if images_base64:
                for b64 in images_base64:
                    try:
                        img_bytes = base64.b64decode(b64)
                        contents.append(types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg"))
                    except Exception as e:
                        logger.warning(f"Failed to decode base64 image frame: {e}")
            contents.append(user_prompt)

            gen_config = types.GenerateContentConfig(
                system_instruction=system_prompt,
                response_mime_type="application/json",
                response_schema=schema,
                http_options=types.HttpOptions(
                    timeout=int(self.request_timeout * 1000),
                    retry_options=types.HttpRetryOptions(attempts=1),
                ),
            )

            # Build list of candidate models (preferred model first, resilient fallback to gemini-3.1-flash-lite)
            candidate_models = [model_name]
            fallback_model = (
                self.models_config.get(f"{task_type}_fallback")
                or self.models_config.get("judge_fallback")
                or self.models_config.get("fallback")
                or "gemini-3.1-flash-lite"
            )
            if fallback_model and fallback_model not in candidate_models:
                candidate_models.append(fallback_model)

            # Estimate token footprint to protect per-model TPM limit (each 360px frame ~180 tokens)
            img_count = len(images_base64) if images_base64 else 0
            est_tokens = (img_count * 180) + (len(user_prompt) // 4) + (len(system_prompt) // 4)

            last_error = None
            for current_model in candidate_models:
                limiter = self.get_rate_limiter(current_model)
                if current_model != model_name:
                    logger.info(
                        f"Primary model '{model_name}' unavailable. "
                        f"Engaging fallback model '{current_model}' (multimodal 500 RPD) for '{task_type}'."
                    )
                for attempt in range(max_retries + 1):
                    try:
                        # Pacing enforced per-model (e.g. 16k TPM for Gemma, 250k TPM for Gemini Flash Lite)
                        limiter.wait_if_needed(estimated_input_tokens=est_tokens)

                        start_time = time.time()
                        # Strict per-call timeout (60-90s) execution wrapper to prevent hangs
                        with ThreadPoolExecutor(max_workers=1) as single_exec:
                            fut = single_exec.submit(
                                self.client.models.generate_content,
                                model=current_model,
                                contents=contents,
                                config=gen_config,
                            )
                            response = fut.result(timeout=self.request_timeout)

                        latency = time.time() - start_time
                        self.call_count += 1

                        in_tok, out_tok = 0, 0
                        if getattr(response, "usage_metadata", None):
                            in_tok = getattr(response.usage_metadata, "prompt_token_count", 0) or 0
                            out_tok = getattr(response.usage_metadata, "candidates_token_count", 0) or 0
                        self.total_input_tokens += in_tok
                        self.total_output_tokens += out_tok

                        # Record actual token usage into per-model sliding window
                        limiter.record_call(input_tokens=in_tok or est_tokens)

                        raw_text = response.text or ""
                        clean_json = self._extract_json(raw_text)
                        parsed = json.loads(clean_json)
                        result = schema.model_validate(parsed)

                        if self.db:
                            self.db.log_llm_call(
                                task_type=task_type,
                                model=current_model,
                                input_tokens=in_tok,
                                output_tokens=out_tok,
                                cost_usd=0.0,
                                latency_s=latency,
                            )

                        return result

                    except (TimeoutError, concurrent.futures.TimeoutError) as te:
                        last_error = te
                        logger.warning(
                            f"Google GenAI ({current_model}) {task_type} request timed out after {self.request_timeout:.0f}s. "
                            f"Attempt {attempt + 1}/{max_retries + 1}."
                        )
                        time.sleep(2.0)
                    except Exception as e:
                        last_error = e
                        err_str = str(e).lower()
                        if "timeout" in err_str or "timed out" in err_str:
                            logger.warning(
                                f"Google GenAI ({current_model}) {task_type} request timed out: {e}. "
                                f"Attempt {attempt + 1}/{max_retries + 1}."
                            )
                            time.sleep(2.0)
                        elif "429" in err_str or "quota" in err_str or "resource_exhausted" in err_str:
                            logger.warning(
                                f"Google GenAI ({current_model}) {task_type} hit 429 rate limit. "
                                "Sleeping 15s before retry..."
                            )
                            time.sleep(15.0)
                        else:
                            logger.warning(f"Google GenAI ({current_model}) {task_type} attempt {attempt + 1} failed: {e}")
                            time.sleep(1.0)

            logger.error(f"Failed structured output for {task_type} with Google GenAI across all models: {last_error}")
            raise RuntimeError(f"Google GenAI call failed for {task_type}: {last_error}")

        # ----------------------------------------------------------------------
        # Anthropic Provider (Claude 3.5 Sonnet / Haiku)
        # ----------------------------------------------------------------------
        content: List[Dict[str, Any]] = []

        # Add vision images if provided
        if images_base64:
            for b64 in images_base64:
                content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": b64,
                    },
                })

        content.append({"type": "text", "text": user_prompt})

        limiter = self.get_rate_limiter(model_name)
        est_tokens = (len(user_prompt) // 4) + (len(system_prompt) // 4) + (len(images_base64) * 280 if images_base64 else 0)

        last_error = None
        for attempt in range(max_retries + 1):
            try:
                limiter.wait_if_needed(estimated_input_tokens=est_tokens)
                start_time = time.time()
                response = self.client.messages.create(
                    model=model_name,
                    max_tokens=1000,
                    system=system_prompt,
                    messages=[{"role": "user", "content": content}],
                )
                latency = time.time() - start_time

                # Track usage
                in_tok = response.usage.input_tokens
                out_tok = response.usage.output_tokens
                cost = self._calculate_cost(model_name, in_tok, out_tok)

                self.call_count += 1
                self.total_input_tokens += in_tok
                self.total_output_tokens += out_tok
                self.total_cost_usd += cost
                limiter.record_call(input_tokens=in_tok or est_tokens)

                raw_text = response.content[0].text
                clean_json = self._extract_json(raw_text)
                parsed = json.loads(clean_json)
                result = schema.model_validate(parsed)

                # Log to DB if available
                if self.db:
                    self.db.log_llm_call(
                        task_type=task_type,
                        model=model_name,
                        input_tokens=in_tok,
                        output_tokens=out_tok,
                        cost_usd=cost,
                        latency_s=latency,
                    )

                return result

            except Exception as e:
                last_error = e
                logger.warning(f"Claude {task_type} attempt {attempt + 1} failed: {e}. Retrying...")
                time.sleep(1.0)

        logger.error(f"Failed structured output for {task_type} after {max_retries + 1} attempts: {last_error}")
        raise RuntimeError(f"Claude call failed for {task_type}: {last_error}")

    def _generate_mock_output(self, task_type: str, user_prompt: str, schema: Type[T]) -> T:
        """Deterministic, intelligent mock generator for offline tests & dry-runs."""
        self.call_count += 1
        # Mock modest tokens and $0.00 actual cost
        in_tok, out_tok = 250, 80
        self.total_input_tokens += in_tok
        self.total_output_tokens += out_tok

        if schema == JudgeResult:
            # Deterministic scoring based on content keywords
            is_broadcast = any(w in user_prompt.lower() for w in ["interview", "broadcast", "movie", "sitcom", "hollywood"])
            is_unfunny = any(w in user_prompt.lower() for w in ["boring", "static", "unboxing", "injury"])
            score = 4.2 if (is_unfunny or is_broadcast) else 8.7
            reason = "Lacks comedic timing or punchline." if is_unfunny else ("Broadcast TV interview media" if is_broadcast else "Great visual comedic timing and relatable mishap.")
            return schema(
                funny_score=score,
                is_english_or_no_speech=True,
                is_safe_for_ads=not is_unfunny,
                has_visible_watermark=False,
                is_broadcast_or_movie=is_broadcast,
                reason=reason,
            )

        elif schema == CaptionResult:
            # Generate unique mock caption and title derived from candidate context
            title_match = re.search(r"Original Post Title:\s*(.+)", user_prompt)
            raw_title = title_match.group(1).strip() if title_match else ""
            if not raw_title or raw_title.lower() in ("none", "unknown", ""):
                raw_title = f"Hilarious Comedy Clip #{self.call_count + 1}"

            # Strip brackets, Reddit tags, and excessive punctuation
            clean_t = re.sub(r"[\[\(\{].*?[\]\)\}]", "", raw_title).strip()
            clean_t = re.sub(r"^[Ww][Cc][Gg][Ww]\s*[-:]*\s*", "", clean_t)  # strip WCGW prefix
            clean_t = re.sub(r"^[Hh][Mm][Bb]\s*[-:]*\s*", "", clean_t)      # strip HMB prefix
            if not clean_t:
                clean_t = f"Comedy Moment #{self.call_count + 1}"

            # Formulate unique caption and YouTube title
            caption_words = clean_t.split()[:7]
            caption_text = " ".join(caption_words) if caption_words else "Funny moment"
            if not any(char in caption_text for char in ("😂", "💀", "🤣")):
                caption_text = f"{caption_text} 😂"

            return schema(
                caption=caption_text,
                youtube_title=f"{clean_t} 😂 #shorts #funny",
                youtube_description=f"Hilarious original clip credit: {raw_title}\n\nSubscribe for more laughs daily!",
            )

        elif schema == PlanResult:
            # Propose fresh allowed subreddits
            allowed_subs = self.config.get("allowed_sources", {}).get("subreddits", ["funny", "Unexpected"])
            allowed_twit = self.config.get("allowed_sources", {}).get("twitter_accounts", ["Cheerfulvids"])
            return schema(
                subreddits=allowed_subs[:3],
                twitter_accounts=allowed_twit[:1],
                strategy_reason="Targeting high-engagement animal and unexpected physical comedy subreddits.",
                proposed_new_sources=[],
            )

        # Phase 3.5 Discovery & Pre-Screen Mock Handlers
        from llm.schemas import (
            DiscoveryResult,
            DiscoveryItem,
            TitlePrescreenResult,
            TitlePrescreenDecision,
            BorderlineRankResult,
            BorderlineRankItem,
        )

        if schema == DiscoveryResult:
            return schema(
                subreddits=[
                    DiscoveryItem(source_type="subreddit", name="ContagiousLaughter", listing_mode="top_month", reason="Organic laughter visual clips"),
                    DiscoveryItem(source_type="subreddit", name="AnimalsBeingDerps", listing_mode="top_week", reason="High engagement animal humor"),
                ],
                search_queries=[
                    DiscoveryItem(source_type="search_query", name="unexpected comedy fail", listing_mode="top_month", reason="Cross-community viral comedy"),
                ],
                explanation="Mock discovery result for dry-run testing.",
            )

        elif schema == TitlePrescreenResult:
            ids = re.findall(r"ID:\s*([a-zA-Z0-9_]+)", user_prompt)
            decisions = []
            for pid in ids:
                decisions.append(TitlePrescreenDecision(post_id=pid, keep=True, drop_reason=None))
            return schema(decisions=decisions)

        elif schema == BorderlineRankResult:
            ids = re.findall(r"ID:\s*([a-zA-Z0-9_]+)", user_prompt)
            ranked = []
            for idx, pid in enumerate(ids, start=1):
                ranked.append(BorderlineRankItem(
                    post_id=pid,
                    rank=idx,
                    calibrated_score=round(8.5 - (idx * 0.4), 2),
                    comparative_reason=f"Mock calibrated comparative rank #{idx}",
                ))
            return schema(ranked_clips=ranked)

        raise ValueError(f"Unknown schema for mock generation: {schema}")

    def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_schema: Type[T],
        task_type: str = "plan",
    ) -> T:
        """Convenience alias for structured text generation."""
        return self.call_structured(
            task_type=task_type,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=response_schema,
        )

