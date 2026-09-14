param(
    [string]$PythonPath = ''
)

$taskSavedErrorAction = $ErrorActionPreference
$taskHadPythonPath = Test-Path Env:PYTHONPATH
$taskSavedPythonPath = $env:PYTHONPATH
$taskPushedLocation = $false

try {
    $ErrorActionPreference = 'Stop'
    if ([string]::IsNullOrWhiteSpace($PythonPath)) {
        $PythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    }
    if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
        throw 'Python interpreter not found. Create .venv or provide -PythonPath with an existing Python executable.'
    }
    # Resolve caller-relative interpreter paths before changing directories.
    $taskPython = (Resolve-Path -LiteralPath $PythonPath).Path
    $taskSourceDir = Join-Path $PSScriptRoot 'src'
    $env:PYTHONPATH = if ([string]::IsNullOrEmpty($taskSavedPythonPath)) {
        $taskSourceDir
    } else {
        $taskSourceDir + [System.IO.Path]::PathSeparator + $taskSavedPythonPath
    }
    Push-Location -LiteralPath $PSScriptRoot
    $taskPushedLocation = $true
    & $taskPython -m unittest discover -s (Join-Path $PSScriptRoot 'tests') -v
    if ($LASTEXITCODE -ne 0) {
        throw "Tests failed with exit code $LASTEXITCODE"
    }
} finally {
    if ($taskPushedLocation) {
        Pop-Location
    }
    if ($taskHadPythonPath) {
        $env:PYTHONPATH = $taskSavedPythonPath
    } else {
        Remove-Item -LiteralPath Env:PYTHONPATH -ErrorAction SilentlyContinue
    }
    $ErrorActionPreference = $taskSavedErrorAction
}
