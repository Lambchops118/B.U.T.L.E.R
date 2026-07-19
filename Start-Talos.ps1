# Start the whole TALOS stack from one place.
#
#   .\Start-Talos.ps1            # open the launcher GUI
#   .\Start-Talos.ps1 --no-gui   # start everything headless in this console
#
# Any arguments are passed straight through to `python -m talos.launcher`.
# The launcher pins the LLM (Ollama) to the RTX 5080 and speech-to-text to the
# RTX 2060, brings up the awareness Postgres container, runs migrations, and
# supervises the main agent, voice worker, and awareness backend together.

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

# The launcher needs tkinter + dotenv, both present in the main-agent venv.
$Candidates = @(
    (Join-Path $ScriptDir ".venv-main\Scripts\python.exe"),
    (Join-Path $ScriptDir ".venv\Scripts\python.exe")
)

$Python = $null
foreach ($c in $Candidates) {
    if (Test-Path $c) { $Python = $c; break }
}
if (-not $Python) {
    $Python = (Get-Command python -ErrorAction SilentlyContinue).Source
}
if (-not $Python) {
    Write-Error "No Python interpreter found (.venv-main missing and 'python' not on PATH)."
    exit 1
}

& $Python -m talos.launcher @args
exit $LASTEXITCODE
