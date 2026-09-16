# Start the Happy Bite backend on Windows - the PowerShell twin of run.sh.
#
#     cd backend
#     .\run.ps1                 # restarts itself when a backend file changes
#     .\run.ps1 -NoReload       # for cooking: no restarts, so no voice re-warming
#
# If PowerShell refuses to run scripts, allow local ones once:
#     Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
#
# Like run.sh it always uses the venv's own python (a `uvicorn` on PATH can
# belong to another interpreter that can't see your packages), and it stops
# whatever already holds the port, so you never talk to a stale server.

param(
    [int]$Port = $(if ($env:PORT) { [int]$env:PORT } else { 8000 }),
    [switch]$NoReload
)

Set-Location $PSScriptRoot

# ---- 1. the venv must exist and be the one we use -------------------------

$Py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) {
    Write-Host "No .venv here." -ForegroundColor Red -NoNewline
    Write-Host " Create one with a supported Python:"
    Write-Host "    py -3.12 -m venv .venv; .venv\Scripts\Activate.ps1"
    Write-Host "    pip install -r requirements-windows.txt"
    exit 1
}

$Version = & $Py -c "import sys; print('%d.%d' % sys.version_info[:2])"
$Minor = [int](& $Py -c "import sys; print(sys.version_info[1])")
if ($Minor -ge 13) {
    Write-Host "The venv is Python $Version." -ForegroundColor Red -NoNewline
    Write-Host " torch and kokoro publish no wheels for it, so pictures and voice"
    Write-Host "cannot work. Rebuild it on 3.12:"
    Write-Host "    deactivate; Remove-Item -Recurse -Force .venv"
    Write-Host "    py -3.12 -m venv .venv; .venv\Scripts\Activate.ps1"
    Write-Host "    pip install -r requirements-windows.txt"
    exit 1
}

# ---- 2. nothing else may be holding the port ------------------------------

$holders = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique
if ($holders) {
    Write-Host "Port $Port was in use by PID(s): $($holders -join ', ') - stopping them." -ForegroundColor Yellow
    foreach ($id in $holders) { Stop-Process -Id $id -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 1
}

# ---- 3. say what will actually run ----------------------------------------

Write-Host "Python $Version" -ForegroundColor Green -NoNewline
Write-Host "  $Py"
@'
import importlib.util as u, shutil
rows = [("faster_whisper", "hearing"), ("kokoro", "speaking"), ("soundfile", "audio out"),
        ("torch", "pictures + Kokoro"), ("diffusers", "pictures"), ("pytesseract", "receipts"),
        ("uvicorn", "the server itself")]
missing = []
for mod, what in rows:
    try:
        ok = u.find_spec(mod) is not None
    except Exception:
        ok = False
    if not ok:
        missing.append((mod, what))
if missing:
    print("  missing from this venv:")
    for mod, what in missing:
        print(f"    - {mod:<16} ({what})")
    print("  pip install -r requirements-windows.txt")
else:
    print("  every local model package present")
try:
    import torch
    print("  torch sees CUDA" if torch.cuda.is_available() else "  torch is CPU-only (see requirements-windows.txt for CUDA)")
except Exception:
    pass
if not shutil.which("ffmpeg"):
    print("  ffmpeg not on PATH (optional):  winget install Gyan.FFmpeg")
'@ | & $Py -

& $Py -c "import uvicorn" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing uvicorn into the venv..." -ForegroundColor Yellow
    & $Py -m pip install -q uvicorn
    if ($LASTEXITCODE -ne 0) { Write-Host "couldn't install uvicorn" -ForegroundColor Red; exit 1 }
}

Write-Host ""
$uvicornArgs = @("-m", "uvicorn", "app.main:app", "--port", "$Port")
if (-not $NoReload) { $uvicornArgs += "--reload" }
& $Py @uvicornArgs
exit $LASTEXITCODE
