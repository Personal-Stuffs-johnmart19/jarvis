"""CLI-only distribution preserves old packages until atomic manifest publication."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest

from integrations import package_companions as packager


@pytest.fixture
def cli_package(tmp_path, monkeypatch):
    monkeypatch.setattr(packager, "ROOT", tmp_path)
    prefix = "terax/" + hashlib.sha256(b"old cli").hexdigest()[:16] + "/"
    old_files = {prefix + "terax-cli.exe": b"old cli", prefix + "LICENSE": b"old Apache License"}
    for relative, contents in old_files.items():
        target = tmp_path / "desktop-bin/companions" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(contents)
    manifest = tmp_path / "integrations/companions.json"
    manifest.parent.mkdir(parents=True)
    catalog = {"schema": 1, "platform": "windows-x86_64", "components": {"terax": {
        "name": "Jarvis terminal CLI", "revision": "b" * 40,
        "executable": prefix + "terax-cli.exe",
        "files": {relative: hashlib.sha256(contents).hexdigest() for relative, contents in old_files.items()}}}}
    manifest.write_text(json.dumps(catalog), encoding="utf-8")
    embedded = tmp_path / "apps/desktop/src-tauri/workbench-core/target/release/terax-cli.exe"
    embedded.parent.mkdir(parents=True)
    embedded.write_bytes(b"new embedded cli")
    provenance = tmp_path / "apps/desktop/workbench/SOURCE.json"
    provenance.parent.mkdir(parents=True)
    provenance.write_text(json.dumps({"revision": "c" * 40}), encoding="utf-8")
    license_path = tmp_path / "third_party/terax/LICENSE"
    license_path.parent.mkdir(parents=True)
    license_path.write_bytes(b"Apache License\nUpstream copyright\n")
    return tmp_path, manifest, catalog, prefix, embedded, license_path


def snapshot(root):
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_cli_only_publication_has_exact_hash_paths_license_and_revision(cli_package):
    root, manifest, _, old_prefix, embedded, license_path = cli_package
    before = snapshot(root)
    packager.package()
    generated = json.loads(manifest.read_text(encoding="utf-8"))
    component = generated["components"]["terax"]
    prefix = "terax/" + hashlib.sha256(embedded.read_bytes()).hexdigest()[:16] + "/"
    expected = {prefix + "terax-cli.exe": embedded.read_bytes(), prefix + "LICENSE": license_path.read_bytes()}
    assert generated["schema"] == 1 and generated["platform"] == "windows-x86_64"
    assert set(generated["components"]) == {"terax"}
    assert component["name"] == "Jarvis terminal CLI"
    assert component["revision"] == component["cli_revision"] == "c" * 40
    assert component["executable"] == prefix + "terax-cli.exe"
    assert component["files"] == {path: hashlib.sha256(data).hexdigest() for path, data in expected.items()}
    for relative, data in expected.items():
        assert (root / "desktop-bin/companions" / relative).read_bytes() == data
    for relative, data in before.items():
        if relative != "integrations/companions.json":
            assert (root / relative).read_bytes() == data
    assert (root / "desktop-bin/companions" / old_prefix / "terax-cli.exe").exists()
    assert not list(root.rglob("*.pending*"))
    first = snapshot(root)
    packager.package()
    assert snapshot(root) == first


@pytest.mark.parametrize("relative", ["../escape.exe", "terax/other/terax-cli.exe", "{prefix}../LICENSE", "C:/outside.exe", "{prefix}NUL.txt", "{prefix}LICENSE."])
def test_hostile_previous_manifest_paths_are_never_followed(cli_package, relative):
    root, manifest, catalog, prefix, _, _ = cli_package
    catalog["components"]["terax"]["files"][relative.format(prefix=prefix)] = "d" * 64
    manifest.write_text(json.dumps(catalog), encoding="utf-8")
    before = snapshot(root)
    packager.package()
    for path, contents in before.items():
        if path != "integrations/companions.json":
            assert (root / path).read_bytes() == contents
    new = json.loads(manifest.read_text())["components"]["terax"]
    assert len(new["files"]) == 2
    assert all(path.startswith(new["executable"].rsplit("/", 1)[0] + "/") for path in new["files"])


@pytest.mark.parametrize("problem", ["empty-built-cli", "missing-apache"])
def test_invalid_inputs_preserve_previous_package(cli_package, problem):
    root, _, _, _, embedded, license_path = cli_package
    if problem == "empty-built-cli":
        embedded.write_bytes(b"")
    else:
        license_path.write_bytes(b"missing attribution")
    before = snapshot(root)
    with pytest.raises(ValueError):
        packager.package()
    assert snapshot(root) == before


def directory_link(link, target, fixture_parent):
    for path in (link, target):
        if (not path.is_absolute() or not path.resolve().is_relative_to(fixture_parent.resolve())
                or any(ord(char) < 32 or char in '&|<>^%!"()' for char in str(path))):
            raise ValueError("Directory-link paths must be owned shell-safe fixture paths.")
    if link.exists() or link.is_symlink():
        raise ValueError("Directory-link fixture destination already exists.")
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            raise
        # Windows directory junctions require no developer-mode/admin privilege.
        from local_mode.setup_state import _system_directory
        command = _system_directory() / "cmd.exe"
        subprocess.run([str(command), "/d", "/c", "mklink", "/J", str(link), str(target)],
                       capture_output=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW)


@pytest.mark.parametrize("location", ["source", "destination"])
def test_fixed_package_paths_reject_directory_links(cli_package, location):
    root, _, _, _, embedded, _ = cli_package
    outside = root.parent / (root.name + "-outside")
    outside.mkdir()
    (outside / "terax-cli.exe").write_bytes(b"outside bytes must stay unchanged")
    if location == "source":
        embedded.unlink()
        link = embedded.parent
        link.rmdir()
    else:
        link = root / "desktop-bin/companions/terax" / hashlib.sha256(embedded.read_bytes()).hexdigest()[:16]
    directory_link(link, outside, root.parent)
    try:
        with pytest.raises(ValueError, match="links"):
            packager.package()
        assert (outside / "terax-cli.exe").read_bytes() == b"outside bytes must stay unchanged"
    finally:
        link.rmdir()  # Unlink the junction/symlink itself, without traversing its target.


def test_unpublished_directory_failure_preserves_previous_package(cli_package, monkeypatch):
    root, _, _, _, _, _ = cli_package
    before = snapshot(root)
    def fail_publish(path, target):
        raise OSError("fixture directory publication failure")
    monkeypatch.setattr(Path, "rename", fail_publish)
    with pytest.raises(OSError, match="directory publication"):
        packager.package()
    assert snapshot(root) == before
    assert not list(root.rglob("*.pending*"))


def test_manifest_failure_leaves_old_identity_and_complete_reusable_package(cli_package, monkeypatch):
    root, manifest, _, _, embedded, license_path = cli_package
    before = snapshot(root)
    original_replace = Path.replace
    def fail_manifest(path, target):
        if target == manifest:
            raise OSError("fixture manifest publication failure")
        return original_replace(path, target)
    monkeypatch.setattr(Path, "replace", fail_manifest)
    with pytest.raises(OSError, match="manifest publication"):
        packager.package()
    for relative, data in before.items():
        assert (root / relative).read_bytes() == data
    destination = root / "desktop-bin/companions/terax" / hashlib.sha256(embedded.read_bytes()).hexdigest()[:16]
    assert {path.name for path in destination.iterdir()} == {"terax-cli.exe", "LICENSE"}
    assert (destination / "terax-cli.exe").read_bytes() == embedded.read_bytes()
    assert (destination / "LICENSE").read_bytes() == license_path.read_bytes()
    assert not list(root.rglob("*.pending*"))
    monkeypatch.setattr(Path, "replace", original_replace)
    packager.package()
    assert json.loads(manifest.read_text())["components"]["terax"]["executable"].endswith("/terax-cli.exe")


def test_existing_hash_directory_is_never_overwritten(cli_package):
    root, _, _, _, embedded, _ = cli_package
    destination = root / "desktop-bin/companions/terax" / hashlib.sha256(embedded.read_bytes()).hexdigest()[:16]
    destination.mkdir()
    (destination / "terax-cli.exe").write_bytes(b"someone else's bytes")
    (destination / "LICENSE").write_bytes(b"another license")
    before = snapshot(root)
    with pytest.raises(ValueError, match="different contents"):
        packager.package()
    assert snapshot(root) == before


def test_new_install_needs_no_old_gui_clone(cli_package):
    root, manifest, _, _, _, _ = cli_package
    manifest.unlink()
    assert not (root / ".cache/terax-upstream").exists()
    packager.package()
    assert json.loads(manifest.read_text())["components"]["terax"]["executable"].endswith("/terax-cli.exe")
