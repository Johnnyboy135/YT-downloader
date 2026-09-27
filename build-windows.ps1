param(
    [string]$Python = "python",
    [string]$IsccPath = "",
    [string]$Version = (Get-Content (Join-Path $PSScriptRoot "VERSION") -Raw).Trim()
)
$ErrorActionPreference = "Stop"
if ($Version -notmatch '^\d+\.\d+\.\d+$') { throw "Version must have the form 1.2.3" }
if (-not $IsccPath) {
    $command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($command) { $IsccPath = $command.Source }
    foreach ($candidate in @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 7\ISCC.exe")) {
        if (-not $IsccPath -and (Test-Path -LiteralPath $candidate)) { $IsccPath = $candidate }
    }
}
if (-not $IsccPath -or -not (Test-Path -LiteralPath $IsccPath)) { throw "Install Inno Setup 6.3+ and pass -IsccPath if it is not on PATH." }
Push-Location $PSScriptRoot
try {
    & $Python -m pip install -r requirements-build.txt
    if ($LASTEXITCODE) { throw "Installing build dependencies failed" }
    & $Python -m unittest discover -s tests -v
    if ($LASTEXITCODE) { throw "Tests failed" }
    & $Python packaging/prepare_windows.py
    if ($LASTEXITCODE) { throw "Preparing bundled tools failed" }
    & $Python -m PyInstaller --noconfirm --clean packaging/windows.spec
    if ($LASTEXITCODE) { throw "PyInstaller failed" }
    & $IsccPath "/DAppVersion=$Version" packaging/windows.iss
    if ($LASTEXITCODE) { throw "Inno Setup failed" }
    Get-ChildItem installer-output -Filter "*.exe"
} finally { Pop-Location }
