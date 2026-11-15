[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$PortableRoot,
    [Parameter(Mandatory=$true)][string]$LoadedModelId,
    [Parameter(Mandatory=$true)][string]$Checkpoint,
    [ValidateSet('success','cancel')][string]$Case = 'success',
    [ValidateSet('cuda','lowvram','cpu')][string]$EngineModeLabel = 'lowvram',
    [string]$CheckpointFile = '',
    [string]$ReportDirectory = '',
    [string]$Prompt = 'A quiet harbor at dusk, digital illustration',
    [int]$Width = 512,
    [int]$Height = 512,
    [int]$Steps = 20,
    [int]$Seed = 12345,
    [switch]$Execute
)
$ErrorActionPreference = 'Stop'
if (-not $ReportDirectory) { $ReportDirectory = Join-Path $PortableRoot 'diagnostics/image-trials' }
New-Item -ItemType Directory -Path $ReportDirectory -Force | Out-Null
$stamp = [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss-fff')
$reportPath = Join-Path $ReportDirectory ("image-trial-" + $stamp + '.json')
$report = [ordered]@{
    criterion = 'AT-09 API, resource and co-existence subchecks; manual acceptance still required'
    status = 'BLOCKED'
    started_at_utc = [DateTime]::UtcNow.ToString('o')
    case = $Case
    human_visual_review = 'NOT_RUN'
    official_release_acceptance = 'NOT_RUN'
    engine_mode_label = $EngineModeLabel
    engine_mode_label_source = 'operator supplied; command line was not inspected'
    request = @{ checkpoint=$Checkpoint; width=$Width; height=$Height; steps=$Steps; seed=$Seed; prompt=$Prompt }
}
$client = $null
$headers = @{}
function Send-App {
    param([string]$Method, [string]$Path, $Body=$null, [switch]$Raw, [hashtable]$ExtraHeaders=@{})
    $message = [System.Net.Http.HttpRequestMessage]::new()
    $message.Method = [System.Net.Http.HttpMethod]::new($Method)
    $message.RequestUri = [Uri]::new($Path.TrimStart('/'), [UriKind]::Relative)
    foreach ($key in $headers.Keys) { [void]$message.Headers.TryAddWithoutValidation($key,[string]$headers[$key]) }
    foreach ($key in $ExtraHeaders.Keys) { [void]$message.Headers.TryAddWithoutValidation($key,[string]$ExtraHeaders[$key]) }
    if ($null -ne $Body) {
        $json = ConvertTo-Json -InputObject $Body -Depth 100 -Compress
        $message.Content = [System.Net.Http.StringContent]::new($json,[Text.Encoding]::UTF8,'application/json')
    }
    $response = $null
    try {
        $response = $client.SendAsync($message).GetAwaiter().GetResult()
        [byte[]]$bytes = $response.Content.ReadAsByteArrayAsync().GetAwaiter().GetResult()
        if (-not $response.IsSuccessStatusCode) {
            $detail = 'request rejected'
            try { $detail = ([Text.Encoding]::UTF8.GetString($bytes) | ConvertFrom-Json).message_ru } catch {}
            throw ("API " + $Path + ': HTTP ' + [int]$response.StatusCode + ' ' + $detail)
        }
        if ($Raw) { return ,$bytes }
        return ([Text.Encoding]::UTF8.GetString($bytes) | ConvertFrom-Json)
    } finally {
        if ($response) { $response.Dispose() }
        $message.Dispose()
    }
}
try {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) { throw 'BLOCKED: Windows is required' }
    $os = Get-CimInstance Win32_OperatingSystem
    $gpus = @(Get-CimInstance Win32_VideoController | Select-Object Name,DriverVersion)
    $report.os = @{ caption=$os.Caption; version=$os.Version; build=$os.BuildNumber }
    $report.physical_video_controllers = $gpus
    if ($os.Caption -notmatch 'Windows 11') { throw 'BLOCKED: target Windows 11 was not confirmed' }
    if (-not @($gpus | Where-Object { $_.Name -match 'RTX\s*5070(?!\s*Ti)' }).Count) { throw 'BLOCKED: target RTX 5070 was not found' }
    if (-not $Execute) { throw 'BLOCKED: pass -Execute to authorize one image workflow and a short LM Studio inference probe' }
    Add-Type -AssemblyName System.Net.Http
    $handler = [System.Net.Http.HttpClientHandler]::new()
    $handler.UseProxy = $false
    $handler.AllowAutoRedirect = $false
    $handler.UseCookies = $true
    $handler.CookieContainer = [System.Net.CookieContainer]::new()
    $client = [System.Net.Http.HttpClient]::new($handler)
    $client.Timeout = [TimeSpan]::FromSeconds(20)
    $instance = Get-Content -LiteralPath (Join-Path $PortableRoot 'data/config/instance.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $port = [int]$instance.port
    if ($port -lt 1 -or $port -gt 65535) { throw 'Invalid application port' }
    $client.BaseAddress = [Uri]::new("http://127.0.0.1:$port/")
    $launcherKey = (Get-Content -LiteralPath (Join-Path $PortableRoot 'data/config/launcher.key') -Raw -Encoding UTF8).Trim()
    $link = Send-App 'POST' '/api/v1/launcher/open' @{} -ExtraHeaders @{'X-Taverna-Launcher'=$launcherKey}
    $fragment = ([Uri]$link.url).Fragment.TrimStart('#')
    if (-not $fragment.StartsWith('launch=')) { throw 'Invalid bootstrap response' }
    $session = Send-App 'POST' '/api/v1/session/bootstrap' @{token=$fragment.Substring(7)}
    $headers['X-Taverna-CSRF'] = $session.csrf
    $report.application_health = Send-App 'GET' '/api/v1/health'
    $beforeModels = @((Send-App 'GET' '/api/v1/models').models)
    if (-not @($beforeModels | Where-Object { $_.type -eq 'llm' -and $_.id -ceq $LoadedModelId }).Count) { throw 'BLOCKED: the requested native LM Studio instance is not already loaded' }
    $report.lm_before = $beforeModels
    $cfg = (Send-App 'GET' '/api/v1/images/settings').config
    if (-not $cfg.payload.enabled) { throw 'BLOCKED: enable the configured image adapter in the UI first' }
    $report.image_settings = $cfg
    $caps = Send-App 'GET' '/api/v1/images/capabilities'
    $report.image_capabilities = $caps
    if (-not ($caps.checkpoints -ccontains $Checkpoint)) { throw 'BLOCKED: the selected local checkpoint is not exposed by the engine' }
    if ($caps.engine_version -match 'fixture|test.only') { throw 'BLOCKED: a test fixture is not a hardware trial' }
    if ($EngineModeLabel -eq 'cpu' -and $caps.devices[0].type -ne 'cpu') { throw 'BLOCKED: CPU mode label does not match the reported device' }
    if ($EngineModeLabel -ne 'cpu' -and $caps.devices[0].type -ne 'cuda') { throw 'BLOCKED: CUDA mode label does not match the reported device' }
    if ($CheckpointFile) { $report.operator_checkpoint_file_sha256 = (Get-FileHash -LiteralPath $CheckpointFile -Algorithm SHA256).Hash }
    $request = @{
        settings_revision=$cfg.revision; name=("Image hardware trial " + $stamp); prompt=$Prompt
        negative_prompt=''; checkpoint=$Checkpoint; width=$Width; height=$Height; steps=$Steps
        cfg=7; seed=$Seed; sampler=$caps.samplers[0]; scheduler=$caps.schedulers[0]
        category='illustration'; tags=@('hardware-trial'); confirmed=$true
    }
    $report.actual_request = $request
    $watch = [Diagnostics.Stopwatch]::StartNew()
    $created = Send-App 'POST' '/api/v1/images/generate' $request -ExtraHeaders @{'Idempotency-Key'=[Guid]::NewGuid().ToString()}
    $report.image_job_id = $created.generation_id
    $deadline = [DateTime]::UtcNow.AddSeconds([int]$cfg.payload.timeout_seconds + [int]$cfg.payload.cancel_wait_seconds + 60)
    $cancelIssued = $false
    do {
        $job = Send-App 'GET' ("/api/v1/images/jobs/" + $created.generation_id)
        if ($Case -eq 'cancel' -and -not $cancelIssued -and $job.state -eq 'running') {
            $events = (Send-App 'GET' ("/api/v1/generations/" + $created.generation_id + '/events?after=0&limit=200')).events
            if (@($events | Where-Object { $_.type -eq 'generation.image_status' -and $_.data.external_status -eq 'in_progress' }).Count) {
                [void](Send-App 'POST' ("/api/v1/generations/" + $created.generation_id + '/cancel') @{})
                $cancelIssued = $true
            }
        }
        if (@('completed','failed','cancelled','interrupted') -contains $job.state) { break }
        if ([DateTime]::UtcNow -gt $deadline) { throw 'Image job observation deadline exceeded; use the UI for external reconciliation' }
        Start-Sleep -Milliseconds 300
    } while ($true)
    $watch.Stop()
    $report.image_elapsed_seconds = $watch.Elapsed.TotalSeconds
    $report.image_job = $job
    $report.image_events = (Send-App 'GET' ("/api/v1/generations/" + $created.generation_id + '/events?after=0&limit=200')).events
    $report.cancellation_requested = $cancelIssued
    if ($Case -eq 'success') {
        if ($job.state -ne 'completed' -or -not $job.result.asset_id) { throw 'Image generation did not complete; inspect the recorded structured error' }
        $asset = Send-App 'GET' ("/api/v1/media/" + $job.result.asset_id + '/metadata')
        [byte[]]$raw = Send-App 'GET' ("/api/v1/media/" + $job.result.asset_id + '/file') -Raw
        $imagePath = Join-Path $ReportDirectory ("image-trial-" + $stamp + '.png')
        [IO.File]::WriteAllBytes($imagePath,$raw)
        $checksum = (Get-FileHash -LiteralPath $imagePath -Algorithm SHA256).Hash
        if ($checksum -ne $asset.payload.checksum) { throw 'Downloaded image checksum mismatch' }
        $report.result_asset = $asset
        $report.downloaded_image_sha256 = $checksum
    } else {
        if (-not $cancelIssued -or $job.state -ne 'cancelled' -or $job.lease.payload.external_status -ne 'cancelled') { throw 'BLOCKED: a running external cancellation was not observed; increase the tested workload and repeat' }
    }
    $afterModels = @((Send-App 'GET' '/api/v1/models').models)
    $report.lm_after = $afterModels
    $beforeIds = ConvertTo-Json -InputObject @($beforeModels.id | Sort-Object -CaseSensitive) -Compress
    $afterIds = ConvertTo-Json -InputObject @($afterModels.id | Sort-Object -CaseSensitive) -Compress
    if ($beforeIds -cne $afterIds) { throw 'Loaded LM Studio instances changed; models are not automatically restored by this test' }
    $probe = Send-App 'POST' '/api/v1/generations' @{model_id=$LoadedModelId; messages=@(@{role='user';content='Reply with one short word.'}); parameters=@{max_tokens=8}} -ExtraHeaders @{'Idempotency-Key'=[Guid]::NewGuid().ToString()}
    $probeDeadline = [DateTime]::UtcNow.AddSeconds(180)
    do {
        $probeJob = Send-App 'GET' ("/api/v1/generations/" + $probe.generation_id)
        if (@('completed','failed','cancelled','interrupted') -contains $probeJob.state) { break }
        if ([DateTime]::UtcNow -gt $probeDeadline) { throw 'LM Studio probe deadline exceeded' }
        Start-Sleep -Milliseconds 300
    } while ($true)
    $report.lm_inference_probe = $probeJob
    if ($probeJob.state -ne 'completed' -or -not $probeJob.result.text) { throw 'Loaded LM Studio instance could not continue inference after the image operation' }
    $report.status = 'PASS'
} catch {
    $report.error = $_.Exception.Message
    $report.status = if ($report.error.StartsWith('BLOCKED:')) { 'BLOCKED' } else { 'FAIL' }
} finally {
    $report.completed_at_utc = [DateTime]::UtcNow.ToString('o')
    $json = ConvertTo-Json -InputObject $report -Depth 100
    [IO.File]::WriteAllText($reportPath,$json,([Text.UTF8Encoding]::new($false)))
    if ($client) { $client.Dispose() }
}
Write-Output ($report.status + ': ' + $reportPath)
if ($report.status -eq 'PASS') { exit 0 }
if ($report.status -eq 'BLOCKED') { exit 2 }
exit 1
