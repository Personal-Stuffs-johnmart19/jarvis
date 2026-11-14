uv pip compile requirements/local.txt --constraint requirements/local.lock.txt --python 3.12 --output-file "$env:RUNNER_TEMP/jarvis-local-resolved.txt" --quiet
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
uv pip compile requirements/full.lock.txt --python 3.12 --output-file "$env:RUNNER_TEMP/jarvis-full-resolved.txt" --quiet
exit $LASTEXITCODE
