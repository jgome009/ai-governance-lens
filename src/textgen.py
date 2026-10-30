"""
Shared synthetic text generation, used by both generate_synthetic_data.py
(ordinary message content, in-memory only, never persisted - see that
module's docstring) and corpus.py (the standalone phase-3 test corpus,
which IS persisted to data/fixtures/).

Keeping this in one module means both call sites size "ordinary" text the
same way, so corpus.py's benign_background entries are representative of
what real messages in northwind.db actually look like.
"""
import hashlib
import random

VOCAB = (
    "project update timeline budget customer ticket release notes meeting "
    "summary draft outline schema query report dashboard metric policy "
    "review feedback status risk compliance roadmap backlog incident "
    "patient record claim invoice contract vendor pipeline deploy test "
    "error log config endpoint token model prompt dataset training".split()
)

# source -> role -> (min_words, max_words). claude_code is deliberately
# much larger than chat tools: real coding-agent turns carry file/diff
# context, chat turns don't (LIMITATIONS.md).
TEXT_LENGTH_WORDS = {
    "chatgpt":     {"user": (15, 120), "assistant": (40, 320)},
    "claude_web":  {"user": (15, 120), "assistant": (40, 320)},
    "gemini":      {"user": (15, 120), "assistant": (40, 320)},
    "claude_code": {"user": (20, 180), "assistant": (400, 4000)},
}

SOURCES = list(TEXT_LENGTH_WORDS.keys())


def gen_text(rng: random.Random, min_words: int, max_words: int) -> str:
    n = rng.randint(min_words, max_words)
    return " ".join(rng.choice(VOCAB) for _ in range(n))


def gen_ordinary_message_text(rng: random.Random, source: str, role: str) -> str:
    min_w, max_w = TEXT_LENGTH_WORDS[source][role]
    return gen_text(rng, min_w, max_w)


def text_stats(text: str):
    char_count = len(text)
    token_count = max(1, round(char_count / 4))  # heuristic estimate, flagged
    text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return char_count, token_count, text_hash
