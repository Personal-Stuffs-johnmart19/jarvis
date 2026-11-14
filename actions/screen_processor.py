"""Compatibility response for the retired automatic screen/camera cloud path."""

from local_mode.messages import OwnedText


CAPTURE_GUIDANCE = OwnedText(
    "Blocked: automatic screen/camera capture and cloud upload are retired. "
    "For screen analysis, request {0} and confirm each frame separately; "
    "captured pixels may be analyzed only by LM Studio at {1}. "
    "For cloud image analysis, manually attach the image in chat. "
    "No screen or camera image was captured or sent.",
    "screen_capture",
    "http://127.0.0.1:1234",
)


def screen_process(parameters: dict, response=None, player=None, session_memory=None) -> str:
    """Keep saved calls actionable without acquiring or transmitting any pixels."""
    return CAPTURE_GUIDANCE
