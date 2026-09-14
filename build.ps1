param(
    [string]$DistDirectory = 'dist',
    [switch]$SkipDependencyInstall
)

$ErrorActionPreference = 'Stop'

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SourceDir = Join-Path $ProjectDir 'src'
$DocsDir = Join-Path $ProjectDir 'docs'
$ExamplesDir = Join-Path $ProjectDir 'examples'
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
    --paths $SourceDir `
    --distpath $DistPath `
    --workpath $WorkPath `
    --specpath $ProjectDir `
    (Join-Path $SourceDir 'teams_caption_tray.py')
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

Copy-Item -LiteralPath (Join-Path $ProjectDir 'README.md') -Destination (Join-Path $DistPath 'README.md') -Force
Copy-Item -LiteralPath $DocsDir -Destination $DistPath -Recurse -Force
Copy-Item -LiteralPath $ExamplesDir -Destination $DistPath -Recurse -Force
Copy-Item -LiteralPath (Join-Path $ProjectDir 'requirements.txt') -Destination (Join-Path $DistPath 'requirements.txt') -Force
Copy-Item -LiteralPath (Join-Path $ProjectDir 'requirements-build.txt') -Destination (Join-Path $DistPath 'requirements-build.txt') -Force

# Keep the frozen app's existing config-example and Setup guide locations.
# The docs/ copy keeps repository-relative links; the root compatibility guide
# gets only its local Markdown links rebased so navigation still works.
Copy-Item -LiteralPath (Join-Path $ExamplesDir 'copilot-config.example.json') -Destination (Join-Path $DistPath 'copilot-config.example.json') -Force
$taskRootGuide = Get-Content -LiteralPath (Join-Path $DocsDir 'CHAT_NOTES_SETUP.md') -Raw -Encoding UTF8
$taskRootGuide = $taskRootGuide -replace '(?<=\]\()([A-Za-z0-9_.-]+\.md)(?=[#)])', 'docs/$1'
$taskRootGuide = $taskRootGuide -replace '\]\(\.\./README\.md\)', '](README.md)'
[System.IO.File]::WriteAllText((Join-Path $DistPath 'CHAT_NOTES_SETUP.md'), $taskRootGuide, [System.Text.UTF8Encoding]::new($false))

Write-Host "Built: $(Join-Path $DistPath 'TeamsCaptionNotes.exe')"
