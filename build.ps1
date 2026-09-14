param(
    [string]$DistDirectory = 'dist',
    [switch]$SkipDependencyInstall
)

$ErrorActionPreference = 'Stop'

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectDir '.venv\Scripts\python.exe'
$DistPath = if ([System.IO.Path]::IsPathRooted($DistDirectory)) { $DistDirectory } else { Join-Path $ProjectDir $DistDirectory }
$BuildLabel = ([System.IO.Path]::GetFileName($DistPath) -replace '[^A-Za-z0-9_.-]', '_')
$WorkPath = Join-Path $ProjectDir ("build-$BuildLabel-" + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Virtual environment not found. Run: py -m venv .venv"
}

if (-not $SkipDependencyInstall) {
    & $Python -m pip install -r (Join-Path $ProjectDir 'requirements-build.txt')
    if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed with exit code $LASTEXITCODE" }
}
& $Python -m PyInstaller `
    --noconfirm `
    --onefile `
    --windowed `
    --name 'TeamsCaptionNotes' `
    --collect-all uiautomation `
    --collect-all comtypes `
    --collect-all pystray `
    --collect-all msal_extensions `
    --distpath $DistPath `
    --workpath $WorkPath `
    --specpath $ProjectDir `
    (Join-Path $ProjectDir 'teams_caption_tray.py')
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

Copy-Item -LiteralPath (Join-Path $ProjectDir 'copilot-config.example.json') -Destination (Join-Path $DistPath 'copilot-config.example.json') -Force
Copy-Item -LiteralPath (Join-Path $ProjectDir 'COPILOT_SETUP.md') -Destination (Join-Path $DistPath 'COPILOT_SETUP.md') -Force
Copy-Item -LiteralPath (Join-Path $ProjectDir 'README.md') -Destination (Join-Path $DistPath 'README.md') -Force
Copy-Item -LiteralPath (Join-Path $ProjectDir 'NEXT_VERSION.md') -Destination (Join-Path $DistPath 'NEXT_VERSION.md') -Force
Copy-Item -LiteralPath (Join-Path $ProjectDir 'CHAT_NOTES_SETUP.md') -Destination (Join-Path $DistPath 'CHAT_NOTES_SETUP.md') -Force
Copy-Item -LiteralPath (Join-Path $ProjectDir 'RELEASE_NOTES-v4.4.md') -Destination (Join-Path $DistPath 'RELEASE_NOTES-v4.4.md') -Force

Write-Host "Built: $(Join-Path $DistPath 'TeamsCaptionNotes.exe')"
