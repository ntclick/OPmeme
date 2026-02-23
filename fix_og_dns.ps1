# OpenGradient DNS Fix Script
# Run this as Administrator to add OpenGradient endpoints to hosts file

$hostsFile = "$env:SystemRoot\System32\drivers\etc\hosts"

# OpenGradient IP mappings (verified working)
$entries = @(
    "3.16.84.142 llm.opengradient.ai"
    "3.18.174.2 llm.opengradient.ai"
    "3.148.53.198 ogevmdevnet.opengradient.ai"
    "18.219.34.190 ogevmdevnet.opengradient.ai"
    "3.16.84.142 sdk-devnet.opengradient.ai"
    "3.18.174.2 sdk-devnet.opengradient.ai"
)

Write-Host "=== OpenGradient Hosts File Fix ===" -ForegroundColor Cyan
Write-Host ""

# Check if running as admin
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")
if (-not $isAdmin) {
    Write-Host "⚠️ Please run PowerShell as Administrator!" -ForegroundColor Red
    Write-Host "   Right-click PowerShell -> Run as Administrator"
    exit 1
}

# Read current hosts file
$currentContent = Get-Content $hostsFile -Raw -ErrorAction SilentlyContinue
if (-not $currentContent) { $currentContent = "" }

# Add entries if not exists
$added = 0
foreach ($entry in $entries) {
    if ($currentContent -notmatch [regex]::Escape($entry)) {
        Add-Content -Path $hostsFile -Value $entry
        Write-Host "✅ Added: $entry" -ForegroundColor Green
        $added++
    } else {
        Write-Host "ℹ️ Already exists: $entry" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "=== Flush DNS Cache ===" -ForegroundColor Cyan
ipconfig /flushdns | Out-Null
Write-Host "✅ DNS cache flushed" -ForegroundColor Green

Write-Host ""
Write-Host "=== Test DNS Resolution ===" -ForegroundColor Cyan

# Test resolutions
$hostsToTest = @("llm.opengradient.ai", "ogevmdevnet.opengradient.ai", "sdk-devnet.opengradient.ai")
foreach ($host in $hostsToTest) {
    try {
        $result = Resolve-DnsName -Name $host -ErrorAction Stop
        $ip = $result[0].IPAddress
        Write-Host "✅ $host -> $ip" -ForegroundColor Green
    } catch {
        Write-Host "❌ $host -> FAILED" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "=== Done! ===" -ForegroundColor Cyan
Write-Host "Please restart your Python application to use the fixed DNS entries."
