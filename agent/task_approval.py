"""Trusted queue suspension and terminal execution signals; never model approval flags."""
from copy import deepcopy

FAILED_STEP_MESSAGE = "Queued step {0} failed. Completed work is preserved. The action may have taken effect; check its state before retrying."
TIMED_OUT_STEP_MESSAGE = "Queued step {0} timed out. Completed work is preserved. Check its outcome before retrying."


class QueueApprovalRequired(RuntimeError):
    def __init__(self, name, args):
        super().__init__("Queued task is waiting for approval")
        self.name = name
        self.args_snapshot = deepcopy(args)


class ToolCancelled(RuntimeError):
    pass


class QueueUiReadRequired(QueueApprovalRequired):
    """A registered read-only UI handler must run in its owning chat closure."""


class QueueUiDispatchRequired(QueueUiReadRequired):
    """An explicitly registered native action needs its canonical UI approval."""


class ToolTimedOut(RuntimeError):
    pass


class TaskExecutionFailed(RuntimeError):
    """An incomplete queued goal; successful earlier actions remain in its checkpoint."""
    def __init__(self, message):
        super().__init__(str(message))
        self.message = message


class ToolNotDispatched(TaskExecutionFailed):
    """Trusted validation failed before entering a queued handler."""


class ToolReportedFailure(TaskExecutionFailed):
    """A handler's actual unsuccessful result, retained without replaying it."""
    def __init__(self, message, result):
        super().__init__(message)
        self.result = deepcopy(result)
