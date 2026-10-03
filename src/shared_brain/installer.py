"""Replace the installation with a verified release, from outside the running process.

Windows cannot replace files that a running program has loaded, so the swap itself is done
by a generated script that waits for every shared-brain process to exit, renames the old
installation aside, moves the staged one into place, checks the new build and rolls back if
anything fails. Nothing here runs without an explicit user request, and processes belonging
to a host are never terminated for the user.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from . import update

STATE_NAME = "update-state.json"
PROCESS_IMAGE = "shared-brain.exe"
WAIT_SECONDS = 90
PACKAGE_ROOT = "shared-brain"
REQUIRED_FILES = ("shared-brain.exe", "desktop/SharedBrain.Desktop.exe", "runtime/node.exe")
REQUIRED_DIRS = ("_internal",)


def state_path(home: Path) -> Path:
    return Path(home) / STATE_NAME


def read_state(home: Path) -> dict:
    try:
        # Windows PowerShell writes UTF8 state files with a BOM; utf-8-sig accepts both.
        payload = json.loads(state_path(home).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_state(home: Path, **values) -> dict:
    path = state_path(home)
    record = {**read_state(home), **values, "at": datetime.now(timezone.utc).isoformat()}
    temporary = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return record


def pending_notice(home: Path) -> str:
    """A one-line report for a replacement that did not finish cleanly."""
    state = read_state(home)
    stage = str(state.get("stage") or "")
    if stage == "replacing":
        return "上次更新在替换过程中中断，请检查安装目录；日志：" + str(state.get("log") or "")
    if stage == "failed":
        return "上次更新失败并已回滚：" + str(state.get("message") or "原因未知")
    return ""


def _no_window() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def list_processes(name: str = PROCESS_IMAGE) -> list[dict]:
    """Processes running from any installation of this program."""
    try:
        completed = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {name}", "/FO", "CSV", "/NH"],
                                   capture_output=True, text=True, timeout=20, creationflags=_no_window())
    except (OSError, subprocess.SubprocessError):
        return []
    rows = []
    for line in completed.stdout.splitlines():
        fields = [field.strip().strip('"') for field in line.split('","')]
        if len(fields) < 2 or not fields[0].lower().endswith(".exe"):
            continue
        try:
            rows.append({"name": fields[0], "pid": int(fields[1])})
        except ValueError:
            continue
    return rows


def foreign_processes() -> list[dict]:
    """Other copies of this program that would keep the installation locked."""
    mine = os.getpid()
    return [row for row in list_processes() if row["pid"] != mine]


def staging_root(install: Path, home: Path, version: str) -> Path:
    """Prefer the installation's own volume so the swap can rename instead of copy."""
    beside = install.parent / f".{install.name}-staging-{version}"
    try:
        beside.parent.mkdir(parents=True, exist_ok=True)
        probe = beside.parent / f".{install.name}-probe"
        probe.mkdir(exist_ok=True)
        probe.rmdir()
        return beside
    except OSError:
        return Path(home) / "updates" / f"staging-{version}"


def extract_archive(archive: Path, target: Path) -> int:
    """Extract into a fresh directory, refusing any entry that escapes it."""
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    root = target.resolve()
    written = 0
    with zipfile.ZipFile(archive) as source:
        for member in source.infolist():
            if member.is_dir():
                continue
            destination = (target / member.filename).resolve()
            if not destination.is_relative_to(root):
                raise update.UpdateError("压缩包里包含指向目录外的路径，已停止更新。")
            destination.parent.mkdir(parents=True, exist_ok=True)
            with source.open(member) as reader, destination.open("wb") as writer:
                shutil.copyfileobj(reader, writer)
            written += 1
    return written


def verify_extracted(app: Path) -> dict:
    for relative in REQUIRED_FILES:
        if not (app / relative).is_file():
            raise update.UpdateError(f"解压后的安装目录缺少 {relative}")
    for relative in REQUIRED_DIRS:
        if not (app / relative).is_dir():
            raise update.UpdateError(f"解压后的安装目录缺少 {relative}/")
    return {"files": sum(1 for path in app.rglob("*") if path.is_file())}


POWERSHELL = """$ErrorActionPreference = 'Stop'
$Install = '{install}'
$Staged = '{staged}'
$Backup = '{backup}'
$Exe = '{exe}'
$State = '{state}'
$Log = '{log}'
$Expect = '{expect}'
$Launch = {launch}
$WaitSeconds = {wait}
$Names = @({names})

function Write-Log([string]$Message) {{
  try {{
    $folder = Split-Path -Parent $Log
    if ($folder -and -not (Test-Path -LiteralPath $folder)) {{ New-Item -ItemType Directory -Force -Path $folder | Out-Null }}
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $Message" | Add-Content -LiteralPath $Log -Encoding UTF8
  }} catch {{ }}
}}
function Write-State([string]$Stage, [string]$Message) {{
  $folder = Split-Path -Parent $State
  if ($folder -and -not (Test-Path -LiteralPath $folder)) {{ New-Item -ItemType Directory -Force -Path $folder | Out-Null }}
  $payload = @{{ stage = $Stage; message = $Message; version = '{version}'; at = (Get-Date).ToString('s') }} |
    ConvertTo-Json -Compress
  Set-Content -LiteralPath $State -Value $payload -Encoding UTF8
}}

try {{
  Write-Log '等待 Shared Brain 退出'
  $deadline = (Get-Date).AddSeconds($WaitSeconds)
  while ((Get-Date) -lt $deadline) {{
    $alive = @(Get-Process -Name $Names -ErrorAction SilentlyContinue)
    if ($alive.Count -eq 0) {{ break }}
    Start-Sleep -Milliseconds 500
  }}
  if (@(Get-Process -Name $Names -ErrorAction SilentlyContinue).Count -gt 0) {{
    throw '程序仍在运行，已放弃替换（未改动任何文件）'
  }}
  if (-not (Test-Path -LiteralPath $Staged)) {{ throw "暂存目录不存在：$Staged" }}
  if (-not (Test-Path -LiteralPath $Exe)) {{ throw "找不到待启动的程序：$Exe" }}
  if ($Expect -ne '') {{
    $reported = (& $Exe --version 2>$null | Out-String)
    if ($reported -notmatch [regex]::Escape($Expect)) {{ throw "新版本自检未通过：$reported" }}
    Write-Log "新版本自检通过：$Expect"
  }}
  if (Test-Path -LiteralPath $Backup) {{ Remove-Item -LiteralPath $Backup -Recurse -Force }}
  Write-Log "备份 $Install -> $Backup"
  Move-Item -LiteralPath $Install -Destination $Backup
  Write-Log "就位 $Staged -> $Install"
  Move-Item -LiteralPath $Staged -Destination $Install
  Write-State 'done' ''
  Write-Log '更新完成'
  if ($Launch) {{ Start-Process -FilePath $Exe; Write-Log '已启动新版本' }}
  if (Test-Path -LiteralPath $Backup) {{ Remove-Item -LiteralPath $Backup -Recurse -Force -ErrorAction SilentlyContinue }}
  $holder = Split-Path -Parent $Staged
  if (Test-Path -LiteralPath $holder) {{ Remove-Item -LiteralPath $holder -Recurse -Force -ErrorAction SilentlyContinue }}
}} catch {{
  Write-Log "失败：$_"
  $message = "$_"
  if (Test-Path -LiteralPath $Backup) {{
    if (Test-Path -LiteralPath $Install) {{ Remove-Item -LiteralPath $Install -Recurse -Force -ErrorAction SilentlyContinue }}
    Move-Item -LiteralPath $Backup -Destination $Install -ErrorAction SilentlyContinue
    Write-Log '已回滚到旧版本'
  }}
  Write-State 'failed' $message
}}
"""


def _literal(value) -> str:
    return str(value).replace("'", "''")


def write_scripts(folder: Path, *, install: Path, staged: Path, backup: Path, exe: Path,
                  state: Path, log: Path, version: str, expect: str = "", launch: bool = True,
                  wait: int = WAIT_SECONDS, names=("shared-brain", "SharedBrain.Desktop")) -> dict:
    """Generate a readable PowerShell script plus a launcher that runs it detached."""
    folder.mkdir(parents=True, exist_ok=True)
    script = folder / "apply-update.ps1"
    quoted = ", ".join("'" + _literal(name) + "'" for name in names)
    script.write_text(POWERSHELL.format(
        install=_literal(install), staged=_literal(staged), backup=_literal(backup), exe=_literal(exe),
        state=_literal(state), log=_literal(log), expect=_literal(expect), version=_literal(version),
        names=quoted, launch="$true" if launch else "$false", wait=int(wait)), encoding="utf-8-sig")
    launcher = folder / "apply-update.cmd"
    launcher.write_text("@echo off\r\npowershell.exe -NoProfile -ExecutionPolicy Bypass -File "
                        f'"%~dp0{script.name}"\r\n', encoding="utf-8")
    return {"script": script, "launcher": launcher, "log": log}


def launch_detached(launcher: Path) -> None:
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "DETACHED_PROCESS", 0)
    subprocess.Popen(["cmd", "/c", str(launcher)], close_fds=True, cwd=str(launcher.parent), creationflags=flags)


def prepare(home: Path, install: Path, version: str, current: str, archive: Path, digest: str,
            launch: bool = True, expect: str | None = None, names=("shared-brain", "SharedBrain.Desktop")) -> dict:
    """Verify, extract and stage everything, without touching the installation yet."""
    if not archive.is_file():
        raise update.UpdateError("找不到已下载的安装包，请先下载并校验。")
    actual = update.file_sha256(archive)
    if not digest or actual != digest.lower():
        raise update.UpdateError("暂存安装包校验失败，已停止更新。")
    root = staging_root(install, home, version)
    extract_archive(archive, root)
    app = root / PACKAGE_ROOT
    layout = verify_extracted(app)
    if expect is None:
        expect = version
    backup = install.with_name(f"{install.name}.bak-{current}")
    scripts = write_scripts(root, install=install, staged=app, backup=backup,
                            exe=install / "shared-brain.exe", state=state_path(home), log=root / "update.log",
                            version=version, expect=expect, launch=launch, names=names)
    return {"staging": root, "staged": app, "backup": backup, "files": layout["files"], **scripts}


def apply(home: Path, install: Path, version: str, current: str, archive: Path, digest: str,
          launch: bool = True, expect: str | None = None, names=("shared-brain", "SharedBrain.Desktop")) -> dict:
    """Check for foreign processes, stage the release and hand the swap to the detached script."""
    busy = foreign_processes()
    if busy:
        return {"started": False, "blocked": busy,
                "message": "检测到其他 Shared Brain 进程仍在运行，请先退出 Codex / Claude Code / DeepSeek Harness "
                           "里的 shared_brain 连接（或重启宿主）后重试。"}
    staged = prepare(home, install, version, current, archive, digest, launch=launch, expect=expect, names=names)
    write_state(home, stage="replacing", version=version, install=str(install), staged=str(staged["staged"]),
                backup=str(staged["backup"]), log=str(staged["log"]), from_version=current)
    launch_detached(staged["launcher"])
    return {"started": True, "staging": str(staged["staging"]), "log": str(staged["log"]),
            "message": "正在退出并替换，程序稍后会自动重启。"}
