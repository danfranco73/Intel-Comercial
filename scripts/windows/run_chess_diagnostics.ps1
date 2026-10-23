# One-shot diagnostic. Only the child diagnostic process may be stopped on timeout.
# __RECEIVER__ and __COLLECTOR_BASE64__ are filled by the local transfer helper.
$ErrorActionPreference = 'Stop'
Write-Host '1/3 Diagnostico iniciado: leyendo estado de las tareas de TMA.'
$receiver = '__RECEIVER__'
$folder = Join-Path $env:TEMP ('IntelComercial-Diagnostico-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $folder | Out-Null
$collector = Join-Path $folder 'collect.py'
$reportPath = Join-Path $folder 'catalog.json'
$bundlePath = Join-Path $folder 'informe.json'
[IO.File]::WriteAllBytes($collector, [Convert]::FromBase64String('__COLLECTOR_BASE64__'))

$tasks = @()
foreach ($name in @('TMA - Stock disponible Chess', 'TMA - Promociones Chess')) {
    $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    foreach ($entry in @($task)) {
        if ($null -eq $entry) { continue }
        try { $info = $entry | Get-ScheduledTaskInfo } catch {
            $tasks += @{ name = $entry.TaskName; status = 'task_info_unavailable' }
            continue
        }
        $actions = @($entry.Actions | ForEach-Object {
            $script = [regex]::Match($_.Arguments, '(?i)"([^"]+\.(?:py|ps1|cmd|bat))"|(\S+\.(?:py|ps1|cmd|bat))')
            @{ executable = $_.Execute; directory = $_.WorkingDirectory; script = $script.Value }
        })
        $tasks += @{ name = $entry.TaskName; state = [string]$entry.State;
            last_run_local = $info.LastRunTime.ToString('o'); result = $info.LastTaskResult;
            actions = $actions }
    }
}
$listeners = @(Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue |
    Select-Object LocalAddress, LocalPort, OwningProcess)
# Read-only: explains whether remote SSH access could work, without changing it.
$ssh = @{ service_present = $false }
$sshService = Get-Service -Name sshd -ErrorAction SilentlyContinue
if ($sshService) {
    $ssh = @{ service_present = $true; status = [string]$sshService.Status;
        start_type = [string]$sshService.StartType }
}
$ssh.port_22_listening = [bool](Get-NetTCPConnection -LocalPort 22 -State Listen -ErrorAction SilentlyContinue)

# Inherit an already configured user secret if this shell predates its creation.
# This affects only the diagnostic process, never the user/machine configuration.
if (-not $env:CHESS_ODBC_PWD) {
    $env:CHESS_ODBC_PWD = [Environment]::GetEnvironmentVariable('CHESS_ODBC_PWD', 'User')
}
$timedOut = $false
$exitCode = $null
Write-Host '2/3 Recopilando diccionario ODBC (tablas, columnas e indices). Maximo: 4 minutos.'
try {
    $process = Start-Process -FilePath 'C:\Python314\python.exe' `
        -ArgumentList @('-I', ('"' + $collector + '"'), '--output', ('"' + $reportPath + '"')) `
        -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $folder 'collector.stdout') `
        -RedirectStandardError (Join-Path $folder 'collector.stderr')
    if (-not $process.WaitForExit(240000)) {
        $timedOut = $true
        $process.Kill()
        $process.WaitForExit()
    }
    $exitCode = $process.ExitCode
} catch {
    # Do not print exception details, environment, command arguments or driver output.
    Write-Host 'El colector no pudo terminar; se enviara el diagnostico disponible.'
}
# The catalog can be several MB: embed the collector's JSON text verbatim instead
# of round-tripping it through PowerShell objects (Windows PowerShell 5.1 rejects
# JSON over ~2 MB). The collector replaces the file atomically, so it is always a
# complete document; the Mac receiver validates it.
$catalogJson = '{"status":"no_report"}'
if (Test-Path $reportPath) {
    $catalogJson = [IO.File]::ReadAllText($reportPath)
}
$meta = @{ tasks = $tasks; gateway_listeners = $listeners; ssh = $ssh;
    collector_timed_out = $timedOut; collector_exit_code = $exitCode } | ConvertTo-Json -Depth 10 -Compress
$json = '{"schema_version":"chess-diagnostic-bundle/2","meta":' + $meta + ',"catalog":' + $catalogJson + '}'
[IO.File]::WriteAllText($bundlePath, $json, [Text.UTF8Encoding]::new($false))
try {
    Write-Host '3/3 Enviando informe directamente por Tailscale. Maximo: 2 minutos.'
    # Tailnet traffic must not be sent to a system or corporate HTTP proxy.
    $reply = & "$env:SystemRoot\System32\curl.exe" --noproxy '*' --connect-timeout 5 --max-time 120 `
        --fail --silent --show-error -H 'Content-Type: application/json; charset=utf-8' `
        --data-binary ('@' + $bundlePath) ($receiver + '/report')
    if ($LASTEXITCODE -ne 0) { throw 'Transfer failed' }
    $response = $reply | ConvertFrom-Json
    if ($response.status -ne 'received') { throw 'No acknowledgement' }
    Write-Host 'INFORME ENTREGADO A LA MAC. Volve al chat: no hace falta copiar resultados.'
} catch {
    Write-Host 'No se pudo entregar el informe. Se conservo en:'
    Write-Host $bundlePath
}
