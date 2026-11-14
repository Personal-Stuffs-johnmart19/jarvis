import ctypes,json,sys,time,subprocess,threading,shutil
from ctypes import wintypes
from pathlib import Path
u=ctypes.WinDLL('user32',use_last_error=True);k=ctypes.WinDLL('kernel32',use_last_error=True)
k.GetConsoleWindow.restype=wintypes.HWND
u.IsWindowVisible.argtypes=[wintypes.HWND];u.IsWindowVisible.restype=wintypes.BOOL
u.GetClassNameW.argtypes=[wintypes.HWND,wintypes.LPWSTR,ctypes.c_int]
k.GetSystemDirectoryW.argtypes=[wintypes.LPWSTR,wintypes.UINT]
system=ctypes.create_unicode_buffer(32768)
assert 0<k.GetSystemDirectoryW(system,len(system))<len(system)
cmd=Path(system.value)/'cmd.exe'
def visible():
 handle=k.GetConsoleWindow()
 return bool(handle and u.IsWindowVisible(handle))
if '--leaf' in sys.argv:
 print(json.dumps({'visible_console':visible(),'arguments':sys.argv[2:]}));time.sleep(.2);sys.exit(0)
callback_type=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
u.EnumWindows.argtypes=[callback_type,wintypes.LPARAM]
def windows():
 found=set()
 @callback_type
 def callback(handle,_):
  name=ctypes.create_unicode_buffer(256);u.GetClassNameW(handle,name,256)
  if u.IsWindowVisible(handle) and any(part in name.value.lower() for part in ('console','cascadia')):found.add(int(handle))
  return True
 u.EnumWindows(callback,0)
 return found
baseline=windows();unexpected=set();stop=threading.Event()
def monitor():
 while not stop.wait(.01):unexpected.update(windows()-baseline)
watch=threading.Thread(target=monitor);watch.start()
leaf=str(Path(__file__).resolve())
ps=shutil.which('pwsh')
node=shutil.which('node')
args=['a b','quote"inside','C:\\trailing\\','Русский']
python_literal=sys.executable.replace("'","''")
leaf_literal=leaf.replace("'","''")
leaf_command=f"& '{python_literal}' '{leaf_literal}' --leaf"
commands=[
 [sys.executable,leaf,'--leaf',*args],
 [ps,'-NoProfile','-NonInteractive','-Command',leaf_command],
 [node,'-e',"const c=require('child_process');let r=c.spawnSync(process.argv[1],[process.argv[2],'--leaf'],{encoding:'utf8',windowsHide:false});process.stdout.write(r.stdout);process.stderr.write(r.stderr);process.exit(r.status)",sys.executable,leaf],
 f'"{cmd}" /d /s /c ""{sys.executable}" "{leaf}" --leaf"',
]
commands.append([ps,'-NoProfile','-NonInteractive','-Command',
 "& pnpm --version | Out-Null; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }; "+leaf_command])
results=[]
try:
 assert not visible(),'Root console was visible'
 for index,command in enumerate(commands):
  result=subprocess.run(command,capture_output=True,text=True,encoding='utf-8',timeout=20)
  assert result.returncode==0,(index,result.stderr)
  data=json.loads(result.stdout);assert not data['visible_console'],(index,data)
  if index==0:assert data['arguments']==args,data
  results.append({'chain':index,'visible_console':False})
finally:stop.set();watch.join()
assert not unexpected,{'new_visible_console_windows':list(unexpected)}
print(json.dumps({'hidden_descendant_chains':results,'new_visible_console_windows':0,'argument_boundaries':True}))
