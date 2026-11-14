# Optional legacy model assets

The public source tree contains this inventory, not the seven model binaries. Existing local files can remain in this directory. [manifest.json](manifest.json) records their exact SHA-256, byte size, owning runtime callers, official source references and known licensing limits. These are local integrity pins; they do not authenticate the original download or grant redistribution rights.

These assets serve optional gesture/face/object detection and the older custom wake-word pipeline. Ordinary chat, tools and the separately configured speech runtimes do not require all seven files. `local_mode/capabilities.py` reports the five vision files as available or missing; `setup_capabilities.py` checks availability without downloading them. `vision/model_manager.py` selects the local YOLO file or the explicit `JARVIS_YOLO_MODEL` override. It is not a download registry.

| Files | Runtime owner | Source and terms |
| --- | --- | --- |
| `embedding_model.onnx`, `melspectrogram.onnx` | `actions/wake_word.py`, `actions/jarvis_wake.py` | [openWakeWord 0.6.0 registry](https://github.com/dscripka/openWakeWord/blob/v0.6.0/openwakeword/__init__.py), v0.5.1 feature assets. Exact converted-weight terms remain unresolved; Apache-2.0 engine code does not settle them. |
| `face_detection_yunet_2023mar.onnx` | `actions/vision_gesture.py` | [OpenCV Zoo YuNet](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet); the model directory states MIT. |
| `face_recognition_sface_2021dec.onnx` | `actions/vision_gesture.py` | [OpenCV Zoo SFace](https://github.com/opencv/opencv_zoo/tree/main/models/face_recognition_sface); its directory includes Apache-2.0 terms. |
| `face_detector.tflite`, `holistic_landmarker.task` | `actions/vision_gesture.py` | Official [face detector](https://developers.google.com/edge/mediapipe/solutions/vision/face_detector) and [holistic landmarker](https://developers.google.com/edge/mediapipe/solutions/vision/holistic_landmarker) catalogs. Exact local variants and weight terms have not been established. |
| `yolo11n.pt` | `vision/model_manager.py`, `actions/vision_gesture.py` | [Ultralytics YOLO11](https://docs.ultralytics.com/models/yolo11/); publisher documents [AGPL-3.0 and separate commercial terms](https://www.ultralytics.com/license). The local release origin is not authenticated. |

## Restore only an optional feature you use

Use the official source page in the manifest, review its model-specific terms and save a compatible asset under the exact listed filename. Compare both byte size and SHA-256 with the manifest before using it. A different digest is a different asset: do not silently replace a pin or assume another model with the same extension is compatible. MediaPipe entries intentionally have no direct download URL because their exact upstream variant is unverified.

The existing openWakeWord package owns its feature-asset download registry. Its `openwakeword.utils.download_models(model_names, target_directory)` helper downloads both ONNX and TFLite feature assets plus VAD; an empty `model_names` list also downloads every bundled classifier. Inspect its selection behavior and terms before invoking it. This manifest adds no parallel downloader and setup does not fetch these optional files automatically. [Upstream licensing clarification](https://github.com/dscripka/openWakeWord/issues/338) remains relevant to the shared feature assets.

Use the locked full optional runtime from `requirements-full.lock.txt`: openWakeWord 0.6.0, ONNX Runtime 1.31.0, OpenCV 5.0.0.93, MediaPipe 1.1.1 and Ultralytics 8.4.183. Package pins describe the existing runtime; they do not establish model identity or model terms. Modern managed model downloads and speech bundles retain their existing registries described in [model downloads](../docs/MODEL_DOWNLOADS.md) and [speech](../docs/SPEECH.md).

Never place recorded wake-word training audio, face profiles, credentials or other private data in source control. Retain upstream notices alongside any separately distributed model assets. Unresolved provenance or license entries must remain visibly unresolved until their exact source and terms are verified.
