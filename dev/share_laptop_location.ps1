# Run in Windows PowerShell 5.1 as the normal laptop user. No administrator rights.
param([string]$SourceUrl)
$ErrorActionPreference = 'Stop'
if (-not $SourceUrl) {
    $SourceUrl = Read-Host 'Paste the Laptop browser URL printed by the Tiny (including #token)'
}
$source = [Uri]$SourceUrl
if ($source.Scheme -ne 'http' -or $source.Host -notin @('127.0.0.1','localhost') -or
    $source.Port -lt 1024 -or $source.UserInfo -or $source.AbsolutePath -ne '/' -or
    $source.Query -or $source.Fragment -notmatch '^#token=([A-Za-z0-9_-]{20,})$') {
    throw 'Use the original http://127.0.0.1:8790/#token=... source URL through the SSH tunnel.'
}
$token = $source.Fragment.Substring(7)
$endpoint = $source.GetLeftPart([UriPartial]::Authority) + '/fix'
$headers = @{ Authorization = 'Bearer ' + $token }
Add-Type -AssemblyName System.Device
$watcher = New-Object System.Device.Location.GeoCoordinateWatcher -ArgumentList ([System.Device.Location.GeoPositionAccuracy]::High)
$watcher.MovementThreshold = 0
function Optional-Number($value) {
    if ([Double]::IsNaN($value) -or [Double]::IsInfinity($value)) { return $null }
    return $value
}
Write-Host 'Using Windows location independently of browser tabs. Keep this terminal and SSH tunnel open.'
Write-Host 'Positions enter normal car history. Ctrl+C stops sharing.'
$previousStatus = ''
try {
    $watcher.Start()
    while ($true) {
        if ($watcher.Permission -eq [System.Device.Location.GeoPositionPermission]::Denied) {
            throw 'Windows denied location. Enable Location services and desktop-app location access in Windows Settings.'
        }
        $position = $watcher.Position
        $coordinate = $position.Location
        $age = ([DateTimeOffset]::UtcNow - $position.Timestamp).TotalSeconds
        if ($coordinate.IsUnknown -or $age -gt 25 -or $age -lt -5) {
            $message = 'Waiting for a fresh Windows location fix. Provider status: ' + $watcher.Status
        } else {
            $speed = Optional-Number $coordinate.Speed
            if ($null -ne $speed -and $speed -lt 0) { $speed = $null }
            $heading = Optional-Number $coordinate.Course
            if ($null -ne $heading -and ($heading -lt 0 -or $heading -gt 360)) { $heading = $null }
            $payload = @{
                latitude = $coordinate.Latitude
                longitude = $coordinate.Longitude
                accuracy = $coordinate.HorizontalAccuracy
                altitude = (Optional-Number $coordinate.Altitude)
                speed = $speed
                heading = $heading
                # Preserve the provider timestamp; never refresh stale coordinates artificially.
                timestamp = $position.Timestamp.ToUnixTimeMilliseconds()
            } | ConvertTo-Json -Compress
            try {
                $result = Invoke-WebRequest -UseBasicParsing -Uri $endpoint -Method Post -Headers $headers -ContentType 'application/json' -Body $payload -TimeoutSec 5
                if ($result.StatusCode -eq 202) {
                    $message = 'Waiting for a fresh Windows fix (check the laptop and Tiny clocks).'
                } else {
                    $message = 'Sharing Windows location; accuracy +/-' + [Math]::Round($coordinate.HorizontalAccuracy) + ' m'
                }
            } catch {
                $message = 'Cannot send location. Check the SSH tunnel and use the current source token. ' + $_.Exception.Message
            }
        }
        if ($message -ne $previousStatus) { Write-Host $message; $previousStatus = $message }
        Start-Sleep -Seconds 2
    }
} finally {
    $watcher.Stop()
    $watcher.Dispose()
    try { Invoke-WebRequest -UseBasicParsing -Uri $endpoint -Method Delete -Headers $headers -TimeoutSec 3 | Out-Null } catch { }
    Write-Host 'Windows location sharing stopped.'
}
