$ErrorActionPreference = "Stop"

$RemoteUser = "root"
$RemoteHost = if ($env:REMOTE_HOST) { $env:REMOTE_HOST } else { "YOUR_SERVER_IP" }
$RemoteDir  = "/opt/landingbot"
$LocalDir   = $PSScriptRoot

$Exclude = @('.env', '.env.example', '*.db', '__pycache__', '*.pyc', '.git', 'venv', 'deploy.ps1', '_remote_setup.sh')

Write-Host "==> Copying files to $RemoteHost..."

$files = Get-ChildItem -Path $LocalDir -File -Recurse | Where-Object {
    $rel = $_.FullName.Substring($LocalDir.Length + 1)
    foreach ($pat in $Exclude) {
        if ($rel -like $pat -or $rel -like "*\$pat" -or $rel -like "*/$pat") { return $false }
    }
    return $true
}

ssh "${RemoteUser}@${RemoteHost}" "mkdir -p $RemoteDir"

foreach ($file in $files) {
    $rel = $file.FullName.Substring($LocalDir.Length + 1) -replace '\\', '/'
    $remoteFile = "$RemoteDir/$rel"
    $parts = $remoteFile -split '/'
    $remoteFileDir = ($parts[0..($parts.Count - 2)]) -join '/'
    ssh "${RemoteUser}@${RemoteHost}" "mkdir -p $remoteFileDir" 2>$null
    scp -q $file.FullName "${RemoteUser}@${RemoteHost}:${remoteFile}"
    Write-Host "  $rel"
}

# Записываем setup-скрипт локально, копируем и запускаем
$setupScript = @'
#!/bin/bash
set -euo pipefail
cd /opt/landingbot
if [ ! -d venv ]; then python3 -m venv venv; fi
venv/bin/pip install -q --upgrade pip
venv/bin/pip install -q -r requirements.txt
cp landingbot.service /etc/systemd/system/landingbot.service
systemctl daemon-reload
systemctl enable landingbot
systemctl restart landingbot
echo "--- status ---"
systemctl status landingbot --no-pager -l
'@

$tmpScript = "$LocalDir\_remote_setup.sh"
[System.IO.File]::WriteAllText($tmpScript, $setupScript, (New-Object System.Text.UTF8Encoding $false))

Write-Host "==> Running setup on server..."
scp -q $tmpScript "${RemoteUser}@${RemoteHost}:${RemoteDir}/_remote_setup.sh"
ssh "${RemoteUser}@${RemoteHost}" "bash $RemoteDir/_remote_setup.sh"

Remove-Item $tmpScript -ErrorAction SilentlyContinue

Write-Host "==> Done."
