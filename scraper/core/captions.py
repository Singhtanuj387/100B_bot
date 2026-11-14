import re
from typing import Optional, Dict, Any


def clean_caption(
    raw_title: str,
    max_words: int = 8,
    preserve_emojis: bool = True,
) -> str:
    """
    Transform a verbose Reddit title or tweet text into a short, punchy social caption.
      - Removes URLs
      - Removes hashtags and @mentions / u/usernames
      - Removes Reddit tags ([OC], (OC), [Video], r/funny, etc.)
      - Cleans punctuation and limits to ~max_words
    """
    if not raw_title:
        return "Wait for the end 😂"

    text = raw_title.strip()

    # 1. Strip URLs
    text = re.sub(r"https?://\S+|www\.\S+", "", text)

    # 2. Strip Reddit tags: [OC], (OC), [cross-post], [OC - audio], etc.
    text = re.sub(r"\[.*?\]|\(.*?\)", "", text)

    # 3. Strip subreddit & user mentions: r/funny, u/user, @twitter_user
    text = re.sub(r"\b[rRuU]/[A-Za-z0-9_]+", "", text)
    text = re.sub(r"@[A-Za-z0-9_]+", "", text)

    # 4. Strip hashtags: #funny, #memes
    text = re.sub(r"#[A-Za-z0-9_]+", "", text)

    # 5. Clean common Reddit prefixes / suffixes
    prefixes = [
        r"^an interesting title:?\s*",
        r"^title:?\s*",
        r"^wait for it:?\s*",
        r"^found this on\s+.*?:?\s*",
    ]
    for p in prefixes:
        text = re.sub(p, "", text, flags=re.IGNORECASE)

    # 6. Normalize whitespace
    text = re.sub(r"\s+", " ", text).strip()

    # 7. Extract emojis so we can preserve at end if desired
    emojis = re.findall(r"[\U00010000-\U0010ffff]", text)
    emoji_suffix = f" {emojis[0]}" if (emojis and preserve_emojis) else " 😂"

    # Remove emojis temporarily to count words accurately
    clean_words_text = re.sub(r"[\U00010000-\U0010ffff]", "", text).strip()
    words = clean_words_text.split()

    if not words:
        return "Bro wasn't expecting this 😂"

    # Trim to max_words
    if len(words) > max_words:
        # Avoid cutting in the middle of a short phrase
        trimmed = " ".join(words[:max_words]).rstrip(",-;:.")
        # If last word is preposition or article, drop it
        if trimmed.split()[-1].lower() in ("the", "a", "an", "and", "or", "to", "in", "of", "with", "for"):
            trimmed = " ".join(trimmed.split()[:-1])
        final_caption = f"{trimmed}{emoji_suffix}"
    else:
        # Keep original length if under max_words, append emoji if missing
        if not emojis and preserve_emojis:
            final_caption = f"{clean_words_text}{emoji_suffix}"
        else:
            final_caption = text

    return final_caption.strip()


def llm_generate_caption(title: str, context: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """
    Pluggable hook for future LLM-based caption generation.
    Returns None by default to use clean_caption.
    When ready, plug in Gemini API / OpenAI / Ollama here.
    """
    # Placeholder: Return None to fall back to rule-based cleaner
    return None

