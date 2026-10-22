<# Read-only guest inventory for the lab's Vulkan investigation.
No task registration, registry writes, driver installation, app .env or API calls.
Run in 64-bit Windows PowerShell using the lab-approved guest account.
#>
$ErrorActionPreference = 'Stop'
$issues = [System.Collections.Generic.List[string]]::new()
$manifests = [System.Collections.Generic.List[string]]::new()
$registry = @()
foreach ($path in @('HKLM:\SOFTWARE\Khronos\Vulkan\Drivers',
                    'HKLM:\SOFTWARE\WOW6432Node\Khronos\Vulkan\Drivers')) {
    try {
        $present = Test-Path -LiteralPath $path
        $entries = @()
        if ($present) {
            $key = Get-Item -LiteralPath $path
            foreach ($name in $key.GetValueNames()) {
                $entries += [pscustomobject]@{ Manifest = $name; Disabled = $key.GetValue($name) }
                if ($name.EndsWith('.json')) { $manifests.Add($name) }
            }
        }
        $registry += [pscustomobject]@{ Path = $path; Present = $present; Entries = $entries }
    } catch { $issues.Add("Registry inventory unavailable: $path ($($_.Exception.GetType().Name))") }
}
$adapters = @()
# Display adapters and software components can both carry ICD discovery values.
foreach ($class in @('{4d36e968-e325-11ce-bfc1-08002be10318}', '{5c4c3332-344d-483c-8739-259e934c9cc8}')) {
    try {
        $path = "HKLM:\SYSTEM\CurrentControlSet\Control\Class\$class"
        foreach ($key in Get-ChildItem -LiteralPath $path) {
            if ($key.PSChildName -notmatch '^\d{4}$') { continue }
            $properties = Get-ItemProperty -LiteralPath $key.PSPath
            $values = @()
            foreach ($name in @('VulkanDriverName', 'VulkanDriverNameWow')) {
                foreach ($value in @($properties.$name)) {
                    if ($value) { $values += [string]$value; $manifests.Add([string]$value) }
                }
            }
            $adapters += [pscustomobject]@{ Class = $class; Slot = $key.PSChildName;
                Description = $properties.DriverDesc; Version = $properties.DriverVersion; Manifests = $values }
        }
    } catch { $issues.Add("Adapter inventory unavailable: $class ($($_.Exception.GetType().Name))") }
}
$manifestInfo = @()
foreach ($file in ($manifests | Sort-Object -Unique)) {
    try {
        $exists = Test-Path -LiteralPath $file -PathType Leaf
        $library = $null
        if ($exists) { $library = (Get-Content -LiteralPath $file -Raw | ConvertFrom-Json).ICD.library_path }
        $manifestInfo += [pscustomobject]@{ Path = $file; Exists = $exists; Library = $library }
    } catch { $issues.Add("Manifest unreadable: $file ($($_.Exception.GetType().Name))") }
}
$loader = Join-Path $env:SystemRoot 'System32\vulkan-1.dll'
$loaderVersion = if (Test-Path -LiteralPath $loader -PathType Leaf) { (Get-Item -LiteralPath $loader).VersionInfo.FileVersion } else { $null }
[pscustomobject]@{
    ContractVersion = 1; CollectedAt = [datetime]::UtcNow.ToString('o')
    Account = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    SessionId = (Get-Process -Id $PID).SessionId; Is64Bit = [Environment]::Is64BitProcess
    Loader = [pscustomobject]@{ Path = $loader; Version = $loaderVersion }
    Registry = $registry; Adapters = $adapters; Manifests = $manifestInfo
    Issues = @($issues.ToArray()); ConfigurationChanged = $false
} | ConvertTo-Json -Depth 10
