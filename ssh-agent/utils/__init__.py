"""通用工具包 (Cross-layer Utilities)"""

from utils.shell import (
    clean_extracted_command,
    detect_os_family,
    is_valid_shell_command,
    join_shell_lines,
    strip_trailing_comment_outside_quotes,
)
from utils.text import EMOJI_PATTERN, TokenBudgetEstimator, remove_emojis, smart_truncate

__all__ = [
    "EMOJI_PATTERN",
    "remove_emojis",
    "smart_truncate",
    "TokenBudgetEstimator",
    "is_valid_shell_command",
    "strip_trailing_comment_outside_quotes",
    "join_shell_lines",
    "clean_extracted_command",
    "detect_os_family",
]
