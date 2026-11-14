from unittest.mock import Mock
import pytest
from local_mode.managed_rpc import dispatch


def test_managed_rpc_routes_exact_identity_and_request_without_loading_catalog():
    manager = Mock()
    manager.list_models.return_value = [{"model_id": "jarvis-managed:llm:test/model@rev"}]
    assert dispatch("managed_chat.list", {}, manager) == {"models": manager.list_models.return_value}
    manager.resolve.assert_not_called()
    args = {"model_id": "jarvis-managed:llm:test/model@rev", "request_id": "request-1"}
    dispatch("managed_chat.resolve", args, manager)
    manager.resolve.assert_called_once_with(**args)
    dispatch("managed_chat.cancel", {"request_id": "request-1"}, manager)
    manager.cancel.assert_called_once_with("request-1")
    dispatch("managed_chat.unload", {"model_id": args["model_id"]}, manager)
    manager.unload.assert_called_once_with(args["model_id"])


@pytest.mark.parametrize("method,args", [
    ("managed_chat.stop", {}),
    ("managed_chat.list", {"path": "elsewhere"}),
    ("managed_chat.resolve", {"model_id": "jarvis-managed:llm:test/model@rev"}),
    ("managed_chat.resolve", {"model_id": "http://elsewhere", "request_id": "r"}),
    ("managed_chat.resolve", {"model_id": "jarvis-managed:x", "request_id": "../r"}),
    ("managed_chat.cancel", {"request_id": ""}),
    ("managed_chat.cancel", {"request_id": "x" * 129}),
    ("managed_chat.cancel", {"request_id": "é"}),
    ("managed_chat.unload", {"model_id": 12}),
])
def test_managed_rpc_rejects_unknown_or_malformed_parameters_before_manager(method,args):
    manager = Mock()
    with pytest.raises(ValueError):
        dispatch(method,args,manager)
    assert manager.mock_calls == []
