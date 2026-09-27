#requires -Version 7.0
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $projectRoot
try {
    New-Item -ItemType Directory -Path build -Force | Out-Null
    $launcher = Join-Path $projectRoot 'build/launcher.py'
    Set-Content -LiteralPath $launcher -Encoding utf8NoBOM -Value @'
from shared_brain.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
'@
    python -m PyInstaller --noconfirm --onedir --console `
        --name shared-brain --paths src --specpath build --workpath build/pyinstaller `
        --collect-data shared_brain --collect-all webview `
        --hidden-import keyring.backends.Windows --hidden-import pystray._win32 `
        $launcher
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }
    Copy-Item -LiteralPath @('README.md', 'LICENSE') -Destination 'dist/shared-brain'
    New-Item -ItemType Directory -Path 'dist/shared-brain/integrations' -Force | Out-Null
    Copy-Item -LiteralPath 'integrations/deepseek-harness.mjs' -Destination 'dist/shared-brain/integrations'
}
finally {
    Pop-Location
}
