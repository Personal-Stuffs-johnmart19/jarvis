"""Owned offline MCP Settings and shared-agent cases for qa_services.py."""
import json
from pathlib import Path
import time
import uuid

import psutil


class McpSettingsQA:
    def __init__(self, root, page, integrations, app_pid):
        self.root, self.page, self.ui, self.app_pid = Path(root), page, integrations, app_pid
        self.directory = self.root / '.cache/verification' / ('mcp-settings-' + uuid.uuid4().hex)
        self.directory.mkdir(parents=True)
        self.config_path = self.root / '.cache/desktop-test/mcp-servers.json'
        self.original = self.config_path.read_bytes() if self.config_path.exists() else None
        self.name = 'MCP_LIFECYCLE_QA_' + uuid.uuid4().hex[:8]
        self.owned = []
        self.exit_owned = []
        self.server_id = None
        self.completed = False

    def invoke(self, command, **args):
        return self.page.evaluate('spec=>window.__JARVIS_NATIVE__.core.invoke(spec.command,spec.args)', {'command': command, 'args': args})

    def wait(self, condition, label, seconds=8):
        end = time.monotonic() + seconds
        while not condition():
            assert time.monotonic() < end, label
            time.sleep(.05)

    def pids(self):
        def read():
            try:
                values = json.loads((self.directory / 'pids.json').read_text())
                return len(values) == 2
            except (OSError, ValueError):
                return False
        self.wait(read, 'MCP fixture did not publish parent/child PIDs')
        processes = [psutil.Process(pid) for pid in json.loads((self.directory / 'pids.json').read_text())]
        assert self.app_pid in [parent.pid for parent in processes[0].parents()], 'MCP fixture is not owned by this QA app'
        self.owned.extend(processes)
        return processes

    def gone(self, processes):
        self.wait(lambda: all(not process.is_running() for process in processes), 'Owned MCP process survived cancellation/stop', 3)

    def card(self):
        return self.ui.locator('article').filter(has=self.ui.get_by_role('heading', name=self.name, exact=True))

    def connect(self):
        (self.directory / 'pids.json').unlink(missing_ok=True)
        self.card().get_by_role('button', name='Connect / Discover', exact=True).click()
        self.ui.get_by_role('region', name='Discovered MCP tools', exact=True).wait_for()
        self.wait(lambda: any(row['id'] == self.server_id and row['connected'] for row in self.invoke('mcp_status')), 'MCP did not connect')
        return self.pids()

    def set_arguments(self, arguments):
        self.card().get_by_role('button', name='Edit', exact=True).click()
        self.ui.get_by_label('Arguments (JSON array)', exact=True).fill(json.dumps(arguments))
        self.ui.get_by_role('button', name='Save', exact=True).click()
        self.card().get_by_role('button', name='Edit', exact=True).wait_for(state='visible')
        self.wait(lambda: self.ui.get_by_label('Arguments (JSON array)', exact=True).count() == 0, 'MCP editor did not finish saving')

    def run(self, request, result, chat):
        original = self.invoke('mcp_config_get')
        self.invoke('mcp_config_save', config={**original, 'servers': [{**item, 'enabled': False} for item in original['servers']]})
        arguments = [str(self.root / 'tests/fixtures/mcp_server.py'), '--pids', str(self.directory / 'pids.json'),
                     '--calls', str(self.directory / 'calls.txt'), '--health-log', str(self.directory / 'health.txt')]
        self.ui.get_by_role('button', name='Add MCP server', exact=True).click()
        self.ui.get_by_label('Name', exact=True).fill(self.name)
        self.ui.get_by_label('Executable', exact=True).fill(str(self.root / '.cache/qa-tools/Scripts/python.exe'))
        self.ui.get_by_label('Arguments (JSON array)', exact=True).fill(json.dumps(arguments))
        self.ui.get_by_label('Working directory (optional)', exact=True).fill(str(self.directory))
        self.ui.get_by_label('Enable this integration and allow Jarvis to connect on demand', exact=True).check()
        self.ui.get_by_label('Default tool policy', exact=False).select_option('ask')
        self.ui.get_by_role('button', name='Save', exact=True).click()
        self.ui.get_by_role('heading', name=self.name, exact=True).wait_for()
        config = self.invoke('mcp_config_get')
        self.server_id = next(item['id'] for item in config['servers'] if item.get('name') == self.name)
        owned = self.connect()
        # Native serialization omits an empty tool_policies map. Discovery must
        # still render both inherited policies without crashing the Settings frame.
        for tool_name in ('echo', 'wait'):
            assert self.ui.get_by_label(tool_name + ' policy', exact=True).input_value() == 'inherit'
        tools = self.invoke('mcp_tools', serverId=self.server_id, includeParameters=True, limit=20)
        assert {tool['name'] for tool in tools['items']} == {'echo', 'wait'}
        assert all(tool['policy'] == 'ask' for tool in tools['items'])
        self.wait(lambda: (self.directory / 'health.txt').read_text().count('tools/list') >= 2, 'Packaged MCP idle health probe did not run', 22)
        self.card().get_by_role('button', name='Stop', exact=True).click()
        self.gone(owned)
        owned = self.connect()
        self.page.get_by_role('button', name='Close Settings', exact=True).click()
        marker = 'MCP_SHARED_ECHO_' + uuid.uuid4().hex
        spec = {'server_id': self.server_id, 'tool_name': 'echo', 'arguments': {'text': marker}}
        request('MCP_SHARED_QA:' + json.dumps(spec))
        approval = chat.get_by_role('button', name='Approve', exact=True)
        approval.first.wait_for(timeout=15000)
        assert not (self.directory / 'calls.txt').exists(), 'MCP tool executed before shared approval'
        approval.first.click()
        assert 'MCP_SHARED_QA_OK' in result(timeout=25)['reply']
        assert (self.directory / 'calls.txt').read_text().splitlines() == ['echo']
        spec['tool_name'], spec['arguments'] = 'wait', {}
        request('MCP_SHARED_QA:' + json.dumps(spec))
        approval.first.wait_for(timeout=15000)
        assert (self.directory / 'calls.txt').read_text().splitlines() == ['echo']
        approval.first.click()
        self.wait(lambda: 'wait' in (self.directory / 'calls.txt').read_text(), 'Approved MCP wait did not start')
        chat.get_by_role('button', name='Stop', exact=True).click()
        self.gone(owned)
        chat.get_by_role('button', name='Send', exact=True).wait_for()
        request('Reply JARVIS_SHARED_READY after MCP cancellation.')
        assert 'JARVIS_SHARED_READY' in result(timeout=25)['reply']
        self.page.get_by_role('button', name='Settings', exact=True).first.click()
        self.page.get_by_role('button', name='Integrations', exact=True).click()
        self.ui.get_by_role('heading', name='Integrations', exact=True).wait_for()
        self.set_arguments(arguments + ['--no-initialize'])
        (self.directory / 'pids.json').unlink(missing_ok=True)
        self.card().get_by_role('button', name='Connect / Discover', exact=True).click()
        owned = self.pids()
        self.card().get_by_text('Connecting…', exact=False).wait_for(timeout=10000)
        stop = self.card().get_by_role('button', name='Stop', exact=True)
        assert stop.is_enabled(), 'Settings Stop is disabled during its own MCP handshake'
        stop.click()
        self.gone(owned)
        self.wait(lambda: not any(row['id'] == self.server_id and row['connecting'] for row in self.invoke('mcp_status')), 'MCP handshake slot was not released')
        self.set_arguments(arguments)
        self.exit_owned = self.connect()
        self.completed = True
        print(json.dumps({'packaged_mcp_settings_stdio': True, 'tools': ['echo', 'wait'], 'idle_health': True,
                          'ui_stop_restart': True, 'shared_approval_before_side_effect': True,
                          'shared_stop_owned_tree': True, 'handshake_ui_stop': True}), flush=True)

    def after_exit(self):
        try:
            self.gone(self.owned)
            if self.completed:
                assert self.exit_owned, 'MCP app-exit fixture was not left connected'
                print(json.dumps({'packaged_mcp_app_exit_owned_tree': True}), flush=True)
        finally:
            if self.original is None:
                self.config_path.unlink(missing_ok=True)
            else:
                self.config_path.write_bytes(self.original)
