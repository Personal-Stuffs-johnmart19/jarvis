"""Canonical queued plans and explicit prior-result values; entirely offline."""
import json
from types import SimpleNamespace

import pytest

from agent import planner, executor
from agent.task_approval import QueueApprovalRequired, TaskExecutionFailed
from local_mode.messages import OwnedRuntimeError, OwnedText


def registry(items):
    return SimpleNamespace(all_descriptors=lambda: items)


def descriptor(name, properties=None, **extra):
    return dict(name=name, description=extra.pop("description", name), parameters={"type": "object", "properties": properties or {}}, **extra)


def fake_model(monkeypatch, responses):
    calls = []

    class Model:
        def __init__(self, **kwargs):
            self.system = kwargs["system_instruction"]

        def generate_content(self, prompt, generation_config):
            calls.append((self.system, prompt, generation_config))
            return SimpleNamespace(text=json.dumps(responses[len(calls) - 1]))

    monkeypatch.setattr("local_mode.model.ContentModel", Model)
    return calls


def plan_step(tool, params=None, number=1):
    return {"step": number, "tool": tool, "parameters": params or {}}


def test_actual_legacy_schema_and_action_descriptions_are_preserved():
    catalog = planner._descriptors()
    from local_mode.tools import DECLARATIONS
    browser = next(d for d in DECLARATIONS if d["name"] == "browser_control")
    assert catalog["browser_control"]["parameters"] == browser["parameters"]
    instruction, shown = planner._instruction(catalog, "Goal: play music", ["browser_control"])
    assert "browser_control" in shown
    supplied = json.loads(instruction.split("Schemas:\n", 1)[1])
    assert supplied["parameters"] == browser["parameters"]
    assert supplied["description"] == browser["description"]
    assert "media_play" in supplied["parameters"]["properties"]["action"]["description"]


def test_registered_service_schema_and_full_name_index_one_request(monkeypatch):
    items = [descriptor("translation.run", {"text": {"type": "string"}}),
             descriptor("custom_tool", {"mode": {"enum": ["current"]}}),
             descriptor("disabled", available=False), descriptor("native_only", executor="native-host")]
    calls = fake_model(monkeypatch, [{"steps": [plan_step("custom_tool", {"mode": "current"})]}])
    plan = planner.create_plan("custom_tool current", registry=registry(items))
    assert plan["steps"][0]["parameters"] == {"mode": "current"}
    assert len(calls) == 1
    assert "translation.run" in calls[0][0] and '"enum":["current"]' in calls[0][0]
    assert "native_only" not in calls[0][0] and "disabled" not in calls[0][0]
    assert len(calls[0][0]) + len(calls[0][1]) <= planner.MAX_INPUT_CHARS
    assert calls[0][2]["max_output_tokens"] == 768


def test_actual_registered_catalog_names_remain_discoverable_with_bounded_browser_schema():
    from local_mode.services import ServiceRegistry
    actual = ServiceRegistry(SimpleNamespace(), lambda: None)
    catalog = planner._descriptors(actual)
    prompt = "Goal: browser_control YouTube play music"
    instruction, shown = planner._instruction(catalog, prompt)
    assert "browser_control" in shown
    assert len(instruction) + len(prompt) <= planner.MAX_INPUT_CHARS
    assert len(shown) <= 3
    for name in catalog:
        assert name in instruction
    assert "translation.run" in catalog and "tasks.status" in catalog


def test_owned_native_queue_fixture_gets_move_schema_in_first_request():
    from local_mode.services import ServiceRegistry
    actual = ServiceRegistry(SimpleNamespace(), lambda: None)
    prompt = 'Goal: QUEUE_APPROVAL_QA:{"source":"D:/.cache/desktop-test/queued-task-owned/queued-source.txt","dest":"D:/.cache/desktop-test/queued-task-owned/queued-destination.txt"}'
    instruction, shown = planner._instruction(planner._descriptors(actual), prompt)
    assert "file_controller" in shown
    assert len(instruction) + len(prompt) <= planner.MAX_INPUT_CHARS


@pytest.mark.parametrize("extra_context", ["", " Browser YouTube music"])
def test_explicit_mcp_name_precedes_common_words_in_actual_registered_catalog(extra_context):
    from local_mode.services import ServiceRegistry
    actual = ServiceRegistry(SimpleNamespace(), lambda: None)
    actual.register_workspace([descriptor("mcp_call", {
        "server_id": {"type": "string"}, "tool_name": {"type": "string"},
        "arguments": {"type": "object"}}, approval="dynamic", queue_dispatch="mcp", queue_path_fields=[])])
    prompt = ('Goal: Use mcp_call once for the exact owned Python fixture. PACKAGED_QUEUE_MCP_APPROVE:'
              '{"server_id":"qa-0d584b310b2344b7b3a801198dcc6f7f","tool_name":"echo","arguments":{"mode":"mcp_approve","text":"PACKAGED_MCP_OK"}}') + extra_context
    instruction, shown = planner._instruction(planner._descriptors(actual), prompt)
    schemas = [json.loads(line) for line in instruction.split("Schemas:\n", 1)[1].splitlines()]
    assert schemas[0]["name"] == "mcp_call" and "mcp_call" in shown
    assert len(shown) <= 3
    assert len(instruction) + len(prompt) <= planner.MAX_INPUT_CHARS


@pytest.mark.parametrize("mention,explicit", [("tasks.status", True), ("`TASKS.STATUS`", True),
    ("tasks.status.extra", False), ("prefix_tasks.status", False), ("tasks.status-suffix", False)])
def test_exact_registered_name_priority_respects_identifier_boundaries(mention, explicit):
    catalog = {name: descriptor(name, description="common words " * 2) for name in ("first", "second", "third")}
    catalog["tasks.status"] = descriptor("tasks.status", description="status")
    instruction, shown = planner._instruction(catalog, "Goal: common words " + mention)
    assert ("tasks.status" in shown) is explicit
    if explicit:
        assert json.loads(instruction.split("Schemas:\n", 1)[1].splitlines()[0])["name"] == "tasks.status"


@pytest.mark.parametrize("mention", ["mcp_call2", "prefix_mcp_call", "mcp_call.extra"])
def test_mcp_name_substrings_do_not_get_explicit_priority(mention):
    catalog = {name: descriptor(name, description="common words") for name in ("first", "second", "third")}
    catalog["mcp_call"] = descriptor("mcp_call")
    _, shown = planner._instruction(catalog, "Goal: common words " + mention)
    assert "mcp_call" not in shown


def test_multiple_explicit_tools_follow_goal_order_without_admitting_unavailable_names():
    catalog = {name: descriptor(name) for name in ("first", "second", "third", "fourth")}
    instruction, shown = planner._instruction(catalog, "Goal: unavailable_name then fourth, second, first, third")
    schemas = [json.loads(line)["name"] for line in instruction.split("Schemas:\n", 1)[1].splitlines()]
    assert schemas == ["fourth", "second", "first"]
    assert shown == set(schemas) and "unavailable_name" not in shown


def test_explicit_priority_keeps_schema_budget_and_discovery_failure():
    catalog = {"large": descriptor("large", description="x" * planner.MAX_INPUT_CHARS), "small": descriptor("small")}
    prompt = "Goal: large then small"
    instruction, shown = planner._instruction(catalog, prompt)
    assert shown == {"small"}
    assert len(instruction) + len(prompt) <= planner.MAX_INPUT_CHARS
    with pytest.raises(ValueError, match="Requested schemas exceed"):
        planner._instruction(catalog, prompt, ["large"])


def test_discovery_loads_exact_schema_once_without_executing_initial_plan(monkeypatch):
    items = [descriptor("read"), descriptor("save")]
    original = planner._instruction

    def instruction(catalog, prompt, requested=None):
        if requested is None:
            return planner.PLANNER_PROMPT + "Registered names: read,save", {"read"}
        return original(catalog, prompt, requested)

    monkeypatch.setattr(planner, "_instruction", instruction)
    steps = [plan_step("read"), plan_step("save", number=2)]
    calls = fake_model(monkeypatch, [{"steps": steps}, {"steps": steps}])
    assert planner.create_plan("read then save", registry=registry(items))["steps"] == steps
    assert len(calls) == 2
    assert '"name":"save"' in calls[1][0] and '"name":"read"' in calls[1][0]


@pytest.mark.parametrize("response", [{"steps": [plan_step("generated_code")]}, {"steps": []},
                                     {"steps": [plan_step("read", number=7)]}])
def test_invalid_plans_fail_locally_without_search_substitution(monkeypatch, response):
    calls = fake_model(monkeypatch, [response])
    with pytest.raises(TaskExecutionFailed):
        planner.create_plan("owned goal", registry=registry([descriptor("read")]))
    assert len(calls) == 1


def test_literals_are_never_overwritten_translated_or_interpolated():
    params = {"action": "write", "content": "42", "name": "{step1}.txt",
              "literal": {"$result": "text", "other": 1}}
    assert executor._inject_context(params, "file_controller", {1: "research " * 200}, "French") == params
    assert executor._inject_context({"content": ""}, "file_controller", {1: "long " * 200}) == {"content": ""}
    assert not hasattr(executor, "_translate_to_goal_language")


def test_explicit_result_paths_are_typed_copied_and_not_recursively_interpreted():
    data = {"result": {"rows": [{"$result": {"step": 99, "path": []}}]}}
    args = {"content": {"$result": {"step": 1, "path": ["result", "rows", 0]}}}
    result = executor._inject_context(args, "file_controller", {1: json.dumps(data)})
    assert result["content"] == data["result"]["rows"][0]
    assert args["content"] == {"$result": {"step": 1, "path": ["result", "rows", 0]}}
    result["content"]["$result"]["step"] = 2
    assert data["result"]["rows"][0]["$result"]["step"] == 99


@pytest.mark.parametrize("reference", [{"step": 2, "path": []}, {"step": True, "path": []},
                                       {"step": 1, "path": ["missing"]}, {"step": 1, "path": [True]}])
def test_invalid_forward_or_missing_references_fail(reference):
    with pytest.raises(ValueError):
        executor._inject_context({"content": {"$result": reference}}, "file_controller", {1: '{"result":"42"}'})
    with pytest.raises(ValueError):
        executor._inject_context({"$result": {"step": 1, "path": []}}, "file_controller", {1: "42"})


def test_reference_resolves_before_approval_then_resume_uses_frozen_arguments(monkeypatch):
    calls = []
    steps = [plan_step("read"), plan_step("write", {"content": {"$result": {"step": 1, "path": ["result"]}}}, 2)]
    monkeypatch.setattr(executor, "create_plan", lambda *_, **__: {"steps": steps})
    monkeypatch.setattr(executor.AgentExecutor, "_summarize", lambda *_: "done")

    def dispatch(name, args, *_args, **_kwargs):
        calls.append((name, args))
        if name == "write":
            raise QueueApprovalRequired(name, args)
        return '{"result":"42"}'

    monkeypatch.setattr(executor, "_call_tool_with_timeout", dispatch)
    checkpoint = {}
    with pytest.raises(QueueApprovalRequired) as raised:
        executor.AgentExecutor().execute("owned", checkpoint=checkpoint)
    assert raised.value.args_snapshot == {"content": "42"}
    assert checkpoint["pending_parameters"] == {"content": "42"}
    checkpoint["plan_results"][1] = '{"result":"changed"}'
    checkpoint["approved_step_result"] = "saved write"
    assert executor.AgentExecutor().execute("owned", checkpoint=checkpoint) == "done"
    assert [name for name, _ in calls] == ["read", "write"]


def test_missing_reference_is_terminal_without_dispatch_or_recovery(monkeypatch):
    monkeypatch.setattr(executor, "create_plan", lambda *_, **__: {"steps": [plan_step("write", {
        "content": {"$result": {"step": 1, "path": []}}})]})
    monkeypatch.setattr(executor, "_call_tool_with_timeout", lambda *_, **__: pytest.fail("must not dispatch"))
    # Invalid references stop before any dispatch or additional planning.
    with pytest.raises(TaskExecutionFailed):
        executor.AgentExecutor().execute("owned")


def test_failed_checkpoint_preserves_earlier_result_without_replanning(monkeypatch):
    original = [plan_step("read"), plan_step("broken", number=2)]
    calls, plans = [], []

    def plan(*_args, **_kwargs):
        plans.append(True)
        return {"steps": original}

    def dispatch(name, args, *_args, **_kwargs):
        calls.append((name, args))
        if name == "broken":
            raise RuntimeError("fixture")
        return name + " result"

    monkeypatch.setattr(executor, "create_plan", plan)
    monkeypatch.setattr(executor, "_call_tool_with_timeout", dispatch)
    checkpoint = {}
    for _ in range(2):
        with pytest.raises(TaskExecutionFailed):
            executor.AgentExecutor().execute("owned", checkpoint=checkpoint)
    assert checkpoint["step_results"] == {1: "read result"}
    assert calls == [("read", {}), ("broken", {})] and len(plans) == 1


def test_runtime_failure_retains_resolved_arguments_and_original_plan(monkeypatch):
    reference = {"$result": {"step": 1, "path": ["value"]}}
    steps = [plan_step("read"), plan_step("write", {"text": reference}, 2)]
    monkeypatch.setattr(executor, "create_plan", lambda *_, **__: {"steps": steps})

    def dispatch(name, args, *_args, **_kwargs):
        if name == "read":
            return '{"value":"exact data"}'
        assert args == {"text": "exact data"}
        raise RuntimeError("fixture")

    monkeypatch.setattr(executor, "_call_tool_with_timeout", dispatch)
    checkpoint = {}
    with pytest.raises(TaskExecutionFailed):
        executor.AgentExecutor().execute("owned", checkpoint=checkpoint)
    assert checkpoint["failed_step"]["parameters"] == {"text": "exact data"}
    assert checkpoint["plan"]["steps"][1]["parameters"] == {"text": reference}
    assert checkpoint["step_results"] == {1: '{"value":"exact data"}'}

@pytest.mark.parametrize("owned", [False, True])
def test_planning_preserves_owned_errors_but_masks_unknown_errors(monkeypatch, owned):
    error = OwnedRuntimeError("The selected-model text helper failed: {0}", "Selected provider text request failed") if owned else RuntimeError("private provider response")

    def fail(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(planner, "_plan", fail)
    with pytest.raises(TaskExecutionFailed) as failed:
        planner.create_plan("owned")
    message = failed.value.message
    assert isinstance(message, OwnedText)
    if owned:
        assert message.template == error.template and message.values == error.values
    else:
        assert message.template == "The queued task has no valid plan. Completed work is preserved."
        assert "private provider response" not in str(message)
