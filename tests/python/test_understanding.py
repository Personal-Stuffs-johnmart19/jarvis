from vision.understanding import SceneCaptioner, VisualQuestionAnswering
from vision.vision_events import VisionEvent


def _events():
    return [
        VisionEvent(type="object_detected", label="person"),
        VisionEvent(type="object_detected", label="laptop"),
        VisionEvent(type="text_detected", label="JARVIS"),
    ]


def test_scene_captioner_uses_local_events():
    caption = SceneCaptioner().caption(_events())
    assert "person" in caption
    assert "laptop" in caption
    assert "JARVIS" in caption


def test_visual_question_answering_answers_counts_and_text():
    vqa = VisualQuestionAnswering()
    assert "1 person" in vqa.answer("how many person objects?", _events())
    assert "JARVIS" in vqa.answer("read the text", _events())
