"""领域层 - 安全策略包 (Domain Policies)"""

from domain.policies.file_access import (
    FORBIDDEN_FILE_PATHS,
    HIGH_RISK_FILE_PATHS,
    SAFE_WRITE_APP_PATHS,
    SENSITIVE_READ_FILE_PATHS,
    audit_file_content_security,
    audit_file_path_security,
)
from domain.policies.intent import (
    has_explicit_read_only_intent,
    is_inspection_command,
    is_multi_target_read_only_prompt,
    is_mutation_already_satisfied,
    is_mutation_task,
    is_read_only_query_prompt,
)

__all__ = [
    "FORBIDDEN_FILE_PATHS",
    "HIGH_RISK_FILE_PATHS",
    "SAFE_WRITE_APP_PATHS",
    "SENSITIVE_READ_FILE_PATHS",
    "audit_file_content_security",
    "audit_file_path_security",
    "has_explicit_read_only_intent",
    "is_read_only_query_prompt",
    "is_multi_target_read_only_prompt",
    "is_mutation_task",
    "is_inspection_command",
    "is_mutation_already_satisfied",
]
