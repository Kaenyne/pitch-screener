# Run a script inside the isolated lseg-data venv.
#
# WHY THIS EXISTS: lseg-data pins pandas<3, and the screener runs on pandas 3.
# Installing lseg-data into the main environment silently downgrades pandas for
# the whole project. So the LSEG client lives in .venv-lseg and nothing else does.
#
# This is safe only because refinitiv.py is cache-first by design: a fetch run in
# here writes data/cache/refinitiv/*.parquet, and the main screener reads those
# parquets with set_offline(True). The two environments share files, never imports.
#
#   .\scripts\lseg.ps1 scripts\refinitiv_smoke.py
#
# It also re-reads REFINITIV_APP_KEY from the User environment on every run,
# because `setx` only reaches processes started after it ran and a long-lived
# shell will otherwise report a perfectly-set key as missing.

param(
    [Parameter(Mandatory = $true, ValueFromRemainingArguments = $true)]
    [string[]]$ScriptAndArgs
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root '.venv-lseg\Scripts\python.exe'

if (-not (Test-Path $py)) {
    Write-Error @"
No lseg venv at $py
Create it with:
    python -m venv .venv-lseg
    .\.venv-lseg\Scripts\python.exe -m pip install -r scripts\requirements-lseg.txt
"@
    exit 1
}

if (-not $env:REFINITIV_APP_KEY) {
    $env:REFINITIV_APP_KEY = [Environment]::GetEnvironmentVariable('REFINITIV_APP_KEY', 'User')
}
if (-not $env:REFINITIV_APP_KEY) {
    Write-Error "REFINITIV_APP_KEY is not set at User scope. Set it with: setx REFINITIV_APP_KEY <key>"
    exit 1
}

# A platform/EDP session reads lseg-data.config.json, which references credentials
# as ${REFINITIV_CLIENT_ID} / ${REFINITIV_CLIENT_SECRET}. lseg-data substitutes an
# UNSET variable with its own literal name rather than failing (_configure.py:131),
# so a missing secret would authenticate as the string "REFINITIV_CLIENT_ID" and
# come back as an opaque 401. Fail here instead, where the cause is legible.
if ($env:REFINITIV_SESSION -and $env:REFINITIV_SESSION -ne 'desktop') {
    foreach ($v in 'REFINITIV_CLIENT_ID', 'REFINITIV_CLIENT_SECRET') {
        if (-not [Environment]::GetEnvironmentVariable($v)) {
            $u = [Environment]::GetEnvironmentVariable($v, 'User')
            if ($u) { Set-Item "env:$v" $u } else {
                Write-Error "REFINITIV_SESSION=$env:REFINITIV_SESSION needs $v, which is not set. See the EDP section of README/refinitiv.py."
                exit 1
            }
        }
    }
}

& $py @ScriptAndArgs
exit $LASTEXITCODE
