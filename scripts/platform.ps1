[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet("init", "start", "stop", "status", "check")]
    [string]$Action = "start",

    [string]$ConfigFile = ""
)

$ErrorActionPreference = "Stop"
$Root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$RunDir = Join-Path $Root ".run"
$LogDir = Join-Path $RunDir "logs"
$BinDir = Join-Path $RunDir "bin"
$PidFile = Join-Path $RunDir "platform-processes.json"
$EnvFile = if ([string]::IsNullOrWhiteSpace($ConfigFile)) {
    Join-Path $Root ".env"
} elseif ([IO.Path]::IsPathRooted($ConfigFile)) {
    [IO.Path]::GetFullPath($ConfigFile)
} else {
    [IO.Path]::GetFullPath((Join-Path $Root $ConfigFile))
}

function Import-DotEnv {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Missing .env. Copy .env.example to .env and configure the platform values."
    }
    foreach ($rawLine in [IO.File]::ReadAllLines($Path)) {
        $line = $rawLine.Trim()
        if (-not $line -or $line.StartsWith("#") -or -not $line.Contains("=")) {
            continue
        }
        $parts = $line -split "=", 2
        $name = $parts[0].Trim()
        $value = $parts[1].Trim().Trim('"').Trim("'")
        if ($name -and -not [Environment]::GetEnvironmentVariable($name, "Process")) {
            [Environment]::SetEnvironmentVariable($name, $value, "Process")
        }
    }
}

function Get-DotEnvValue {
    param([string]$Path, [string]$Name)
    if (-not (Test-Path -LiteralPath $Path)) {
        return ""
    }
    foreach ($rawLine in [IO.File]::ReadAllLines($Path)) {
        $line = $rawLine.Trim()
        if (-not $line -or $line.StartsWith("#") -or -not $line.Contains("=")) {
            continue
        }
        $parts = $line -split "=", 2
        if ($parts[0].Trim() -eq $Name) {
            return $parts[1].Trim().Trim('"').Trim("'")
        }
    }
    return ""
}

function Set-DotEnvValue {
    param([string]$Path, [string]$Name, [string]$Value)
    $lines = New-Object 'System.Collections.Generic.List[string]'
    if (Test-Path -LiteralPath $Path) {
        foreach ($line in [IO.File]::ReadAllLines($Path)) {
            $lines.Add($line)
        }
    }
    $updated = $false
    for ($index = 0; $index -lt $lines.Count; $index++) {
        $line = $lines[$index].Trim()
        if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
            $parts = $line -split "=", 2
            if ($parts[0].Trim() -eq $Name) {
                $lines[$index] = "$Name=$Value"
                $updated = $true
                break
            }
        }
    }
    if (-not $updated) {
        $lines.Add("$Name=$Value")
    }
    [IO.File]::WriteAllLines($Path, $lines, (New-Object Text.UTF8Encoding($false)))
}

function New-RandomBase64 {
    param([int]$ByteCount)
    $bytes = New-Object byte[] $ByteCount
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
    }
    finally {
        $rng.Dispose()
    }
    return [Convert]::ToBase64String($bytes)
}

function Read-PlainSecret {
    param([string]$Prompt)
    $secure = Read-Host $Prompt -AsSecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
}

function Initialize-PlatformEnv {
    $example = Join-Path $Root ".env.example"
    if (-not (Test-Path -LiteralPath $EnvFile)) {
        if (-not (Test-Path -LiteralPath $example)) {
            throw "Missing .env.example."
        }
        Copy-Item -LiteralPath $example -Destination $EnvFile
        Write-Host "Created .env from .env.example."
    }

    $databaseUrl = Get-DotEnvValue $EnvFile "PLATFORM_DATABASE_URL"
    if ([string]::IsNullOrWhiteSpace($databaseUrl)) {
        $databaseUrl = Read-Host "PostgreSQL URL (for example postgresql://agent_platform:password@127.0.0.1:5432/agent_platform_dev)"
    }
    if (-not $databaseUrl.StartsWith("postgresql://") -and
        -not $databaseUrl.StartsWith("postgresql+psycopg://")) {
        throw "A PostgreSQL PLATFORM_DATABASE_URL is required."
    }

    $adminPassword = Get-DotEnvValue $EnvFile "BOOTSTRAP_ADMIN_PASSWORD"
    if ([string]::IsNullOrWhiteSpace($adminPassword)) {
        $adminPassword = Read-PlainSecret "Initial administrator password"
    }
    if ([string]::IsNullOrWhiteSpace($adminPassword)) {
        throw "BOOTSTRAP_ADMIN_PASSWORD cannot be empty."
    }

    $internalToken = Get-DotEnvValue $EnvFile "INTERNAL_API_TOKEN"
    if ([string]::IsNullOrWhiteSpace($internalToken) -or
        $internalToken.StartsWith("replace-with-")) {
        $internalToken = New-RandomBase64 48
    }
    $modelKey = Get-DotEnvValue $EnvFile "MODEL_MASTER_KEY"
    if ([string]::IsNullOrWhiteSpace($modelKey)) {
        $modelKey = New-RandomBase64 32
    }

    Set-DotEnvValue $EnvFile "PLATFORM_DATABASE_URL" $databaseUrl
    Set-DotEnvValue $EnvFile "PLATFORM_REPOSITORY_BACKEND" "postgres"
    Set-DotEnvValue $EnvFile "INTERNAL_API_TOKEN" $internalToken
    Set-DotEnvValue $EnvFile "MODEL_MASTER_KEY" $modelKey
    Set-DotEnvValue $EnvFile "MODEL_GATEWAY_ENABLED" "true"
    Set-DotEnvValue $EnvFile "MODEL_GATEWAY_URL" "http://127.0.0.1:8080"
    Set-DotEnvValue $EnvFile "PLATFORM_TASK_API_ENABLED" "true"
    Set-DotEnvValue $EnvFile "PYTHON_RUNTIME_URL" "http://127.0.0.1:8000"
    Set-DotEnvValue $EnvFile "GATEWAY_ADDRESS" ":8080"
    Set-DotEnvValue $EnvFile "SESSION_COOKIE_SECURE" "false"
    Set-DotEnvValue $EnvFile "BOOTSTRAP_ADMIN_PASSWORD" $adminPassword

    Write-Host "Local platform configuration is ready."
    Write-Host "Next: .\platform.cmd check"
}
function Require-Value {
    param([string]$Name)
    $value = [Environment]::GetEnvironmentVariable($Name, "Process")
    if ([string]::IsNullOrWhiteSpace($value)) {
        throw "$Name must be configured in .env."
    }
}

function Test-TcpPort {
    param([int]$Port)
    $client = New-Object Net.Sockets.TcpClient
    try {
        $result = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        if (-not $result.AsyncWaitHandle.WaitOne(300)) {
            return $false
        }
        $client.EndConnect($result)
        return $true
    }
    catch {
        return $false
    }
    finally {
        $client.Dispose()
    }
}

function Get-RecordedProcesses {
    if (-not (Test-Path -LiteralPath $PidFile)) {
        return @()
    }
    try {
        return @((Get-Content -LiteralPath $PidFile -Raw | ConvertFrom-Json))
    }
    catch {
        return @()
    }
}

function Get-ManagedProcess {
    param($Item)
    if (-not $Item.path) {
        return $null
    }
    $process = Get-Process -Id ([int]$Item.pid) -ErrorAction SilentlyContinue
    if (-not $process) {
        return $null
    }
    try {
        $actualPath = [IO.Path]::GetFullPath($process.Path)
        $expectedPath = [IO.Path]::GetFullPath([string]$Item.path)
        if (-not $actualPath.Equals($expectedPath, [StringComparison]::OrdinalIgnoreCase)) {
            return $null
        }
    }
    catch {
        return $null
    }
    return $process
}

function Stop-Platform {
    foreach ($item in (Get-RecordedProcesses)) {
        $process = Get-ManagedProcess $item
        if ($process) {
            Stop-Process -Id $process.Id -Force
            Write-Host ("Stopped {0} (PID {1})" -f $item.name, $process.Id)
        }
    }
    if (Test-Path -LiteralPath $PidFile) {
        Remove-Item -LiteralPath $PidFile -Force
    }
}

function Show-Status {
    $items = Get-RecordedProcesses
    if ($items.Count -eq 0) {
        Write-Host "Platform is not running (no PID file)."
    }
    foreach ($item in $items) {
        $process = Get-ManagedProcess $item
        $state = if ($process) { "running" } else { "stopped" }
        Write-Host ("{0,-8} {1,-8} PID {2}" -f $item.name, $state, $item.pid)
    }
    $runtimeState = if (Test-TcpPort 8000) { "reachable" } else { "offline" }
    $gatewayState = if (Test-TcpPort 8080) { "reachable" } else { "offline" }
    Write-Host ("Runtime  http://127.0.0.1:8000/ui  {0}" -f $runtimeState)
    Write-Host ("Gateway  http://127.0.0.1:8080     {0}" -f $gatewayState)
}

function Wait-Http {
    param([string]$Url, [int]$Seconds = 30)
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
                return $true
            }
        }
        catch {
            Start-Sleep -Milliseconds 500
        }
    }
    return $false
}

if ($Action -eq "init") {
    Initialize-PlatformEnv
    exit 0
}
if ($Action -eq "stop") {
    Stop-Platform
    exit 0
}
if ($Action -eq "status") {
    Show-Status
    exit 0
}

Set-Location -LiteralPath $Root
Import-DotEnv $EnvFile

$env:PLATFORM_REPOSITORY_BACKEND = "postgres"
$env:PLATFORM_TASK_API_ENABLED = "true"
$env:MODEL_GATEWAY_ENABLED = "true"
$env:MODEL_GATEWAY_URL = "http://127.0.0.1:8080"
$env:PYTHON_RUNTIME_URL = "http://127.0.0.1:8000"
$env:GATEWAY_ADDRESS = ":8080"
$env:SESSION_COOKIE_SECURE = "false"

foreach ($name in @(
    "PLATFORM_DATABASE_URL",
    "INTERNAL_API_TOKEN",
    "MODEL_MASTER_KEY",
    "BOOTSTRAP_ADMIN_EMAIL",
    "BOOTSTRAP_ADMIN_PASSWORD",
    "BOOTSTRAP_WORKSPACE_NAME",
    "BOOTSTRAP_WORKSPACE_SLUG"
)) {
    Require-Value $name
}

$existing = @(
    Get-RecordedProcesses | Where-Object { $null -ne (Get-ManagedProcess $_) }
)
if ($existing.Count -gt 0) {
    throw "Platform processes are already running. Use scripts/platform.ps1 status."
}

try {
    $modelKeyBytes = [Convert]::FromBase64String($env:MODEL_MASTER_KEY)
}
catch {
    throw "MODEL_MASTER_KEY must be valid Base64."
}
if ($modelKeyBytes.Length -ne 32) {
    throw "MODEL_MASTER_KEY must decode to exactly 32 bytes."
}
if ($env:INTERNAL_API_TOKEN.Length -lt 32) {
    throw "INTERNAL_API_TOKEN must contain at least 32 characters."
}
if (-not $env:PLATFORM_DATABASE_URL.StartsWith("postgresql://") -and
    -not $env:PLATFORM_DATABASE_URL.StartsWith("postgresql+psycopg://")) {
    throw "PLATFORM_DATABASE_URL must be a PostgreSQL URL."
}
if (Test-TcpPort 8000) {
    throw "Port 8000 is already in use."
}
if (Test-TcpPort 8080) {
    throw "Port 8080 is already in use."
}

$python = (Get-Command python -ErrorAction Stop).Source
$go = (Get-Command go -ErrorAction Stop).Source
& $python -c "import fastapi, uvicorn, psycopg, sqlalchemy"
if ($LASTEXITCODE -ne 0) {
    throw 'Python dependencies are missing. Run: python -m pip install -e ".[platform]"'
}

if ($Action -eq "check") {
    & $python -c "import os, psycopg; connection = psycopg.connect(os.environ['PLATFORM_DATABASE_URL']); connection.close()"
    if ($LASTEXITCODE -ne 0) {
        throw "PostgreSQL connection check failed."
    }
    & $go version
    if ($LASTEXITCODE -ne 0) {
        throw "Go toolchain check failed."
    }
    Write-Host "Configuration, Python dependencies, Go, and PostgreSQL connection are ready."
    exit 0
}
New-Item -ItemType Directory -Force $RunDir, $LogDir, $BinDir | Out-Null
Write-Host "Applying PostgreSQL migrations..."
& $python -m scripts.postgres_migrate --database-url $env:PLATFORM_DATABASE_URL
if ($LASTEXITCODE -ne 0) {
    throw "PostgreSQL migration failed."
}

$gatewayExe = Join-Path $BinDir "gateway.exe"
$bootstrapExe = Join-Path $BinDir "bootstrap.exe"
Write-Host "Building Go Gateway..."
Push-Location (Join-Path $Root "gateway")
try {
    & $go build -o $gatewayExe ./cmd/gateway
    if ($LASTEXITCODE -ne 0) {
        throw "Gateway build failed."
    }
    & $go build -o $bootstrapExe ./cmd/bootstrap
    if ($LASTEXITCODE -ne 0) {
        throw "Bootstrap build failed."
    }
}
finally {
    Pop-Location
}

Write-Host "Bootstrapping workspace and administrator..."
& $bootstrapExe
if ($LASTEXITCODE -ne 0) {
    throw "Workspace bootstrap failed."
}

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$runtimeParams = @{
    FilePath = $python
    ArgumentList = @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000")
    WorkingDirectory = $Root
    PassThru = $true
    WindowStyle = "Hidden"
    RedirectStandardOutput = Join-Path $LogDir "runtime-$stamp.log"
    RedirectStandardError = Join-Path $LogDir "runtime-$stamp.err.log"
}
$runtime = Start-Process @runtimeParams

$gatewayParams = @{
    FilePath = $gatewayExe
    WorkingDirectory = $Root
    PassThru = $true
    WindowStyle = "Hidden"
    RedirectStandardOutput = Join-Path $LogDir "gateway-$stamp.log"
    RedirectStandardError = Join-Path $LogDir "gateway-$stamp.err.log"
}
$gateway = Start-Process @gatewayParams

$workerParams = @{
    FilePath = $python
    ArgumentList = @("-m", "scripts.platform_worker")
    WorkingDirectory = $Root
    PassThru = $true
    WindowStyle = "Hidden"
    RedirectStandardOutput = Join-Path $LogDir "worker-$stamp.log"
    RedirectStandardError = Join-Path $LogDir "worker-$stamp.err.log"
}
$worker = Start-Process @workerParams

$processes = @(
    [ordered]@{ name = "runtime"; pid = $runtime.Id; path = $python },
    [ordered]@{ name = "gateway"; pid = $gateway.Id; path = $gatewayExe },
    [ordered]@{ name = "worker"; pid = $worker.Id; path = $python }
)
$processes | ConvertTo-Json | Set-Content -LiteralPath $PidFile -Encoding UTF8

$runtimeReady = Wait-Http "http://127.0.0.1:8000/ui"
$gatewayReady = Wait-Http "http://127.0.0.1:8080/health/ready"
Start-Sleep -Milliseconds 500
$workerAlive = $null -ne (Get-Process -Id $worker.Id -ErrorAction SilentlyContinue)

if (-not $runtimeReady -or -not $gatewayReady -or -not $workerAlive) {
    Write-Host "Startup failed. Logs are in $LogDir"
    Stop-Platform
    exit 1
}

Write-Host ""
Write-Host "Platform started successfully."
Write-Host "Web UI:  http://127.0.0.1:8080"
Write-Host "Login:   $env:BOOTSTRAP_ADMIN_EMAIL"
Write-Host "Logs:    $LogDir"
Write-Host "Stop:    powershell -NoProfile -ExecutionPolicy Bypass -File scripts/platform.ps1 stop"
