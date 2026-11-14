import os, subprocess, sys, time
from pathlib import Path
path=Path(sys.argv[1])
if len(sys.argv)>2 and sys.argv[2]=='child':
    grandchild=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],shell=False,close_fds=True)
    path.write_text(f'{os.getppid()} {os.getpid()} {grandchild.pid}')
    time.sleep(60)
else:
    # Audited: the current interpreter runs this fixed CARGO_MANIFEST_DIR fixture;
    # pidfile path is literal argv data and the mode is constant, never shell/code.
    # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-tainted-env-args.dangerous-subprocess-use-tainted-env-args
    child=subprocess.Popen([sys.executable,__file__,str(path),'child'],shell=False,close_fds=True)
    deadline=time.monotonic()+5
    while not path.exists():
        if time.monotonic()>deadline: raise RuntimeError('owned child startup failed')
        time.sleep(.01)
    if len(sys.argv)>2 and sys.argv[2]=='exit-root': sys.exit(0)
    time.sleep(60)
