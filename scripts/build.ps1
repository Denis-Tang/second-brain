#requires -Version 7.0
param([string]$OutputDirectory = 'dist')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $projectRoot
try {
    New-Item -ItemType Directory -Path build -Force | Out-Null
    $launcher = Join-Path $projectRoot 'build/launcher.py'
    $iconPath = Join-Path $projectRoot 'src/shared_brain/web/icon.ico'
    Set-Content -LiteralPath $launcher -Encoding utf8NoBOM -Value @'
import ctypes
import msvcrt
import os
import sys

# Windowed PyInstaller sets stdio to None; reconnect inherited MCP/Hook pipes.
if getattr(sys, "frozen", False):
    get_handle = ctypes.windll.kernel32.GetStdHandle
    get_handle.restype = ctypes.c_void_p
    for name, number, mode, flags in (("stdin", -10, "r", os.O_RDONLY),
                                      ("stdout", -11, "w", os.O_WRONLY),
                                      ("stderr", -12, "w", os.O_WRONLY)):
        handle = get_handle(number)
        fd = msvcrt.open_osfhandle(handle, flags) if handle not in (None, ctypes.c_void_p(-1).value) else os.open(os.devnull, flags)
        stream = os.fdopen(fd, mode, encoding="utf-8", buffering=1)
        setattr(sys, name, stream)
        setattr(sys, "__" + name + "__", stream)

from shared_brain.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
'@
    python -m PyInstaller --noconfirm --onedir --windowed `
        --distpath $OutputDirectory --icon $iconPath `
        --name shared-brain --paths src --specpath build --workpath build/pyinstaller `
        --collect-data shared_brain --collect-all webview `
        --hidden-import keyring.backends.Windows --hidden-import pystray._win32 `
        $launcher
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }
    $packageDirectory = Join-Path $OutputDirectory 'shared-brain'
    Copy-Item -LiteralPath @('README.md', 'LICENSE') -Destination $packageDirectory
    $integrationDirectory = Join-Path $packageDirectory 'integrations'
    New-Item -ItemType Directory -Path $integrationDirectory -Force | Out-Null
    Copy-Item -Path 'integrations/*.mjs' -Destination $integrationDirectory
    Copy-Item -LiteralPath 'integrations/README.md' -Destination $integrationDirectory
    $nodeExecutable = @(Get-Command node -CommandType Application)[0].Source
    $nodeVersion = & $nodeExecutable --version
    $runtimeDirectory = Join-Path $packageDirectory 'runtime'
    New-Item -ItemType Directory -Path $runtimeDirectory -Force | Out-Null
    Copy-Item -LiteralPath $nodeExecutable -Destination (Join-Path $runtimeDirectory 'node.exe')
    Invoke-WebRequest -Uri "https://raw.githubusercontent.com/nodejs/node/$nodeVersion/LICENSE" `
        -OutFile (Join-Path $runtimeDirectory 'NODE-LICENSE.txt')
}
finally {
    Pop-Location
}
