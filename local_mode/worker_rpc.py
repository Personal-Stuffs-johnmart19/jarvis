"""Bounded JSON RPC for isolated workers, without consoles or inherited API keys."""
from __future__ import annotations
import json
import math
import os
import subprocess
import sys
from local_mode.config import ROOT
from local_mode.messages import OwnedValueError, OwnedRuntimeError
from local_mode.processes import run


def request(module: str, payload: bytes, timeout: int) -> dict:
    environment=dict(os.environ)
    for name in ('GEMINI_API_KEY','GOOGLE_API_KEY','JARVIS_GEMINI_API_KEY','PLAYWRIGHT_MCP_EXTENSION_TOKEN'):
        environment.pop(name,None)
    worker = module.rsplit('.', 1)[-1]
    try:
        result=run([sys.executable,'-X','utf8','-m',module],input=payload,capture_output=True,timeout=timeout,cwd=ROOT,env=environment)
    except subprocess.TimeoutExpired as error:
        raise OwnedRuntimeError('Local worker {0} timed out after {1} seconds.', worker, timeout) from error
    if result.returncode != 0:
        raise OwnedRuntimeError('Local worker {0} exited with code {1}. Check the installed packages and models.', worker, result.returncode)
    if len(result.stdout)>300000:
        raise OwnedRuntimeError('Local worker response exceeds the size limit.')
    try:
        data=json.loads(result.stdout.decode('utf8'))
    except (ValueError, RecursionError) as error:
        raise OwnedRuntimeError('Invalid local worker response.') from error
    if not isinstance(data,dict):
        raise OwnedRuntimeError('Invalid local worker response.')
    error = data.get('error')
    if error:
        if (isinstance(error, dict) and error.get('source') == 'jarvis'
                and isinstance(error.get('message'), str) and 0 < len(error['message']) <= 4096
                and isinstance(error.get('values'), list) and len(error['values']) <= 16
                and all(value is None or isinstance(value, (bool, int))
                        or isinstance(value, str) and len(value) <= 4096
                        or isinstance(value, float) and math.isfinite(value)
                        for value in error['values'])):
            error_type = OwnedRuntimeError if data.get('error_kind') == 'runtime' else OwnedValueError
            raise error_type(error['message'], *error['values'])
        raise ValueError(str(error)[:1500])
    return data
