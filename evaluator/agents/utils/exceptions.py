"""Exception classes for agent functionality"""


class ToolRejectedError(Exception):
    """Raised when a tool execution is rejected by the user"""
    pass


class TaskInterruptedError(Exception):
    """Raised when a task is interrupted by the user"""
    pass


class InvalidToolJSONError(Exception):
    """Raised when LLM emits malformed JSON for a tool_use input"""
    pass


class InvalidToolInputError(Exception):
    """Raised when LLM emits invalid tool input"""
    pass

class InvalidLLMResponseError(Exception):
    """Raised when LLM response is empty"""
    pass


class ContentPolicyViolationError(Exception):
    """Raised when LLM refuses to respond due to content policy/safety filters"""
    pass