"""Generate reviewable project drafts; installation and execution are separate tools."""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from local_mode.messages import OwnedText
from local_mode.owned_process import ProcessCancelled, check_action_cancelled

PROJECTS_DIR = Path.home() / "Desktop" / "JarvisProjects"
MAX_FILES = 32
MAX_FILE_BYTES = 128 * 1024
MAX_PROJECT_BYTES = 1024 * 1024


def _get_model():
    from local_mode.model import ContentModel
    return ContentModel("gemini-3.6-flash")


def _strip_fences(text):
    text = text.strip()
    text = re.sub(r"^```[a-zA-Z]*\r?\n?", "", text)
    return re.sub(r"\r?\n?```\s*$", "", text).strip()


def _relative_path(value):
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ValueError("Invalid project file path")
    for part in value.split("/"):
        if (not part or part in {".", ".."} or len(part) > 255 or part.endswith((".", " "))
                or any(ord(char) < 32 or char in '\\:*?"<>|' for char in part)
                or re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])", part.split(".")[0])):
            raise ValueError("Project file paths must be safe relative paths")
    return value


def _validate_plan(plan):
    if not isinstance(plan, dict):
        raise ValueError("Project plan must be an object")
    files = plan.get("files")
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES:
        raise ValueError("Project plan must contain 1 to 32 files")
    paths = set()
    for info in files:
        if not isinstance(info, dict):
            raise ValueError("Invalid project file description")
        path = _relative_path(info.get("path"))
        if path.casefold() in paths:
            raise ValueError("Duplicate project file path")
        paths.add(path.casefold())
        if not isinstance(info.get("description", ""), str) or len(info.get("description", "")) > 4000:
            raise ValueError("Project file description is too long")
    # Reject file/directory collisions before creating a project or calling a writer.
    for path in paths:
        if any(parent.as_posix().casefold() in paths for parent in Path(path).parents if str(parent) != "."):
            raise ValueError("Project files have conflicting paths")
    entry = _relative_path(plan.get("entry_point"))
    if entry not in {info["path"] for info in files}:
        raise ValueError("Project entry point must be a planned file")
    dependencies = plan.get("dependencies", [])
    if (not isinstance(dependencies, list) or len(dependencies) > 64
            or any(not isinstance(item, str) or not item.strip() or len(item) > 200 for item in dependencies)):
        raise ValueError("Invalid project dependency list")
    command = plan.get("run_command", "")
    if not isinstance(command, str) or len(command) > 1000:
        raise ValueError("Invalid proposed run command")
    return files, entry, dependencies, command


def _generate(model, prompt, max_bytes):
    check_action_cancelled()
    try:
        response = model.generate_content(prompt)
    except Exception:
        check_action_cancelled()
        raise
    check_action_cancelled()
    text = response.text
    if not isinstance(text, str) or not text.strip() or len(text) > max_bytes:
        raise ValueError("Generated project content is empty or exceeds its size limit")
    text = _strip_fences(text)
    if not text or len(text.encode("utf-8")) > max_bytes:
        raise ValueError("Generated project content is empty or exceeds its size limit")
    return text


def _publish_draft(destination, content):
    """Publish a staged draft only if no other writer created the destination."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    published = False
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=destination.parent,
                                         prefix=f".{destination.name}.", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        check_action_cancelled()
        # link is atomic and refuses an existing destination. Unsupported
        # filesystems fail closed instead of falling back to an overwriting save.
        os.link(temporary, destination)
        published = True
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                # A published file belongs in saved_files even if cleanup of
                # the staging link fails. Leave that link for later cleanup.
                if not published:
                    raise


def dev_agent(parameters, response=None, player=None, session_memory=None, speak=None):
    p = parameters or {}
    saved = []
    project_dir = None
    review = {}
    try:
        description = p.get("description", "")
        language = p.get("language", "python")
        name = p.get("project_name", "")
        if (not isinstance(description, str) or not description.strip() or len(description) > 16000
                or not isinstance(language, str) or len(language) > 64
                or not isinstance(name, str) or len(name) > 128):
            raise ValueError("Invalid project generation arguments")
        check_action_cancelled()
        model = _get_model()
        raw = _generate(model, f"Plan a minimal {language} project for: {description}\n"
                        "Return only JSON with project_name, entry_point, files (path and description), "
                        "dependencies (package names only), and run_command (for later review only). "
                        "Use 1 to 32 files, relative forward-slash paths, and dependency order. "
                        "No installation or execution will occur.", 64 * 1024)
        plan = json.loads(raw)
        files, entry, dependencies, command = _validate_plan(plan)
        name = name or plan.get("project_name", "jarvis_project")
        if not isinstance(name, str) or not name or len(name) > 128:
            raise ValueError("Invalid project name")
        _relative_path(name)
        if "/" in name:
            raise ValueError("Project name must be one directory name")
        check_action_cancelled()
        PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
        project_dir = PROJECTS_DIR.resolve() / name
        # A fresh directory prevents an approved generation intent from replacing
        # an existing project. Choose a new name to generate another draft.
        project_dir.mkdir(exist_ok=False)
        review = {"entry_point": entry, "dependencies": dependencies, "proposed_run_command": command,
                  "execution_pending": True, "requires_separate_approval": True}
        total = 0
        for info in files:
            check_action_cancelled()
            destination = (project_dir / info["path"]).resolve()
            if project_dir not in destination.parents or destination.exists():
                raise ValueError("Project destination escapes its folder or already exists")
            code = _generate(model, f"Write only the complete {language} source for {info['path']}.\n"
                             f"Project goal: {description}\nFile purpose: {info.get('description', '')}\n"
                             f"Project plan: {json.dumps(files, ensure_ascii=False)}\n"
                             "Use the stated project paths. Return raw source only.", MAX_FILE_BYTES)
            total += len(code.encode("utf-8"))
            if total > MAX_PROJECT_BYTES:
                raise ValueError("Generated project exceeds the 1 MiB size limit")
            check_action_cancelled()
            if destination.exists() or destination.is_symlink() or project_dir not in destination.resolve().parents:
                raise ValueError("Project destination changed during generation")
            _publish_draft(destination, code)
            saved.append(info["path"])
        check_action_cancelled()
        return {"ok": True, "status": "saved", "project_dir": str(project_dir), "saved_files": saved,
                **review, "result": OwnedText("Project saved to {0}. Review the files, dependencies and proposed command; installation and execution require separate approved tools.", str(project_dir))}
    except Exception as error:
        cancelled = isinstance(error, ProcessCancelled)
        return {"ok": False, "status": "cancelled" if cancelled else "partial_failed" if saved else "failed",
                "cancelled": cancelled, "project_dir": str(project_dir) if project_dir else None,
                "saved_files": saved, **review,
                "error": OwnedText("Project generation stopped: {0}. Saved files are preserved; no installation or execution was performed.", str(error))}
