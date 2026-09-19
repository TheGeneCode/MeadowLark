"""Dictionary utilities for merging and manipulating yt-dlp options."""

from typing import Any


def merge_dicts_recursive(
    base: dict[str, Any],
    overrides: dict[str, Any],
) -> dict[str, Any]:
    """
    Recursively merge overrides into base without mutating inputs.

    Args:
        base: Base dictionary to merge into.
        overrides: Dictionary whose values take precedence.

    Returns:
        New dictionary with merged values.

    Behavior:
        - Dicts are merged recursively.
        - Lists are extended when both sides are lists.
        - Other values are replaced by overrides.
    """

    def _merge(a: Any, b: Any) -> Any:  # noqa: ANN401 - generic merge over arbitrary option values
        if isinstance(a, dict) and isinstance(b, dict):
            out = dict(a)
            for k, v in b.items():
                if k in out:
                    out[k] = _merge(out[k], v)
                else:
                    out[k] = v
            return out
        if isinstance(a, list) and isinstance(b, list):
            return [*a, *b]
        return b if b is not None else a

    return _merge(base, overrides)


DEFAULT_POSTPROCESSORS: list[dict[str, Any]] = [
    {"key": "SponsorBlock"},
    {
        "key": "ModifyChapters",
        "remove_sponsor_segments": ["sponsor", "selfpromo"],
    },
]


def remove_sponsorblock_postprocessor(opts: dict[str, Any]) -> dict[str, Any]:
    """
    Return a copy of yt-dlp options with SponsorBlock postprocessing removed.

    This allows downloads to succeed even when SponsorBlock is temporarily
    unavailable (e.g., API 503).

    Args:
        opts: yt-dlp options dictionary.

    Returns:
        New dictionary with SponsorBlock postprocessor removed.
    """
    # Shallow copy to avoid mutating caller's dict
    opts_copy = dict(opts)
    postprocs = opts_copy.get("postprocessors")
    if not isinstance(postprocs, list):
        return opts_copy

    opts_copy["postprocessors"] = [
        pp for pp in postprocs if not (isinstance(pp, dict) and pp.get("key") == "SponsorBlock")
    ]
    return opts_copy
