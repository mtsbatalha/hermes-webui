"""Format helpers extracted from api/config.py.

Re-exported via ``api.config`` so ``from api.config import _format_ollama_label``
keeps working.  No external module should import from ``api._cfg.format_labels``
directly.
"""

from __future__ import annotations

_AMBIENT_GH_CLI_MARKERS = frozenset({"gh_cli", "gh auth token"})

# Environment variable sources that are auto-detected and should be filtered
# when the token is a classic PAT (ghp_*) that Copilot API doesn't support.
# Note: COPILOT_GITHUB_TOKEN is NOT included here - it's user-specific config.
_AMBIENT_GH_ENV_SOURCES = frozenset({"env:github_token", "env:gh_token"})


def _is_ambient_gh_cli_entry(source: str, label: str, key_source: str) -> bool:
    """True when a credential-pool entry is a seeded gh-cli token rather than
    one the user added explicitly. Filter these so Copilot doesn't appear in
    the dropdown just because `gh` is installed on the system.

    Also filters GITHUB_TOKEN and GH_TOKEN env var entries, which are
    auto-detected from the environment and should not cause Copilot to appear
    in the picker when the token is a classic PAT (ghp_*) that Copilot API
    doesn't support.

    Note: COPILOT_GITHUB_TOKEN is NOT filtered - it's user-specific config
    that should always be respected.
    """
    source_lower = source.strip().lower()
    return (
        source_lower in _AMBIENT_GH_CLI_MARKERS
        or source_lower in _AMBIENT_GH_ENV_SOURCES
        or label.strip().lower() == "gh auth token"
        or key_source.strip().lower() == "gh auth token"
    )


def _format_ollama_label(mid: str) -> str:
    """Turn an Ollama model id (Ollama tag format) into a readable display label.

    Examples: 'kimi-k2.5' → 'Kimi K2.5', 'qwen3-vl:235b-instruct' → 'Qwen3 VL (235B Instruct)'
    """
    name_part, _, variant = mid.partition(":")

    def _fmt(s: str) -> str:
        tokens = s.replace("-", " ").replace("_", " ").split()
        out = []
        for t in tokens:
            alpha_only = t.replace(".", "")
            if alpha_only.isalpha() and len(t) <= 3:
                out.append(t.upper())  # short acronym: glm → GLM, vl → VL, gpt → GPT
            elif alpha_only.isalnum() and alpha_only and alpha_only[0].isdigit():
                out.append(t.upper())  # size param: 235b → 235B, 1t → 1T
            else:
                out.append(t[0].upper() + t[1:] if t else t)  # capitalize: kimi → Kimi
        return " ".join(out)

    label = _fmt(name_part)
    if variant:
        label += f" ({_fmt(variant)})"
    return label


def _format_nous_label(mid: str) -> str:
    """Turn a Nous Portal model id into a readable display label.

    Nous IDs are ``<vendor>/<model>[:<variant>]`` (e.g. ``anthropic/claude-opus-4.7``);
    drop the vendor namespace, prettify the model name with the same token
    rules as :func:`_format_ollama_label` (short acronyms uppercase, size
    suffixes uppercase, capitalize the rest), then append ``" (via Nous)"``
    so the entry is visually distinct from same-named models in other
    provider groups (e.g. direct Anthropic).

    Examples (matches the helper's actual output — labels are produced by
    :func:`_format_ollama_label`'s token rules, so 3-letter tokens like
    ``GPT`` and ``PRO`` render uppercase)::

        anthropic/claude-opus-4.7         -> Claude Opus 4.7 (via Nous)
        openai/gpt-5.4-mini               -> GPT 5.4 Mini (via Nous)
        google/gemini-3.1-pro-preview     -> Gemini 3.1 PRO Preview (via Nous)
        moonshotai/kimi-k2.6              -> Kimi K2.6 (via Nous)
        qwen/qwen3.5-plus-02-15           -> Qwen3.5 Plus 02 15 (via Nous)
        nvidia/nemotron-3-super-120b-a12b -> Nemotron 3 Super 120B A12b (via Nous)
        minimax/minimax-m2.5:free         -> MiniMax M2.5 (Free) (via Nous)
    """
    name_part = mid.split("/", 1)[-1] if "/" in mid else mid
    # MiniMax-CN ids come back lowercase on the live wire (`minimax-m2.5`) but
    # the curated label convention is mixed-case "MiniMax M2.5" — match that.
    if name_part.lower().startswith("minimax"):
        name_part = "MiniMax" + name_part[len("minimax"):]
    base = _format_ollama_label(name_part)
    return f"{base} (via Nous)"
