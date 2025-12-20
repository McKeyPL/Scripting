<#
.SYNOPSIS
  Copy vSS Port Groups (name + VLAN) from all ESXi hosts in a Datacenter to a vDS.
.RUN
.\Copy-vSS-to-vDS.ps1 -vCenter "vcsa01.company.local" -DatacenterName "Production DC 1" -VdsName "Prod vDS 01" -NumPorts 256
.NOTES
  - VLAN 4095 on vSS (VGT trunk) -> vDS trunk range 0-4094
  - De-duplicates across hosts
  - If same PG name has different VLAN IDs across hosts, it will WARN and SKIP that PG.
#>

param(
  [Parameter(Mandatory=$true)]
  [string]$vCenter,

  [Parameter(Mandatory=$true)]
  [string]$DatacenterName,

  [Parameter(Mandatory=$true)]
  [string]$VdsName,

  [int]$NumPorts = 128,

  # Optional: exclude defaults or anything you don't want copied
  [string[]]$ExcludePortGroups = @("VM Network", "Management Network")
)

Write-Host "Connecting to vCenter: $vCenter" -ForegroundColor Cyan
Connect-VIServer -Server $vCenter | Out-Null

# Resolve Datacenter + VDS (names with spaces are fine)
$dc  = Get-Datacenter -Name $DatacenterName -ErrorAction Stop
$vds = Get-VDSwitch -Name $VdsName -Location $dc -ErrorAction Stop

Write-Host "Target Datacenter: $($dc.Name)" -ForegroundColor Cyan
Write-Host "Target vDS:        $($vds.Name)" -ForegroundColor Cyan

# Get all hosts in the datacenter
$hosts = Get-VMHost -Location $dc -ErrorAction Stop
if (-not $hosts -or $hosts.Count -eq 0) {
  Write-Warning "No ESXi hosts found in Datacenter '$DatacenterName'."
  Disconnect-VIServer -Server $vCenter -Confirm:$false
  return
}

Write-Host "Found $($hosts.Count) host(s) in datacenter. Collecting vSS port groups..." -ForegroundColor Cyan

# Collect vSS port groups from each host, then de-dupe
$allPgs = foreach ($h in $hosts) {
  # Get all port groups visible on that host
  Get-VirtualPortGroup -VMHost $h | ForEach-Object {
    # Standard switch PGs have a VirtualSwitch backing
    # (Distributed PGs may show empty/other backing depending on version)
    [PSCustomObject]@{
      HostName    = $h.Name
      PgName      = $_.Name
      VlanId      = $_.VlanId
      SwitchName  = if ($_.VirtualSwitch) { $_.VirtualSwitch.Name } else { "" }
      IsStandard  = [bool]$_.VirtualSwitch
    }
  }
}

# Keep only standard (vSS) PGs and apply exclusions
$vssPgs = $allPgs |
  Where-Object { $_.IsStandard -eq $true } |
  Where-Object { $ExcludePortGroups -notcontains $_.PgName }

if (-not $vssPgs -or $vssPgs.Count -eq 0) {
  Write-Warning "No vSS port groups found in Datacenter '$DatacenterName' (after exclusions). Nothing to do."
  Disconnect-VIServer -Server $vCenter -Confirm:$false
  return
}

# Detect conflicts: same PG name but different VLAN IDs across hosts
$conflicts = $vssPgs |
  Group-Object PgName |
  Where-Object { ($_.Group | Select-Object -ExpandProperty VlanId -Unique).Count -gt 1 }

if ($conflicts.Count -gt 0) {
  Write-Warning "Detected port group name conflicts (same name, different VLANs across hosts). These will be SKIPPED:"
  foreach ($c in $conflicts) {
    $vlans = ($c.Group | Select-Object -ExpandProperty VlanId -Unique) -join ","
    $hostsList = ($c.Group | Select-Object -ExpandProperty HostName -Unique) -join ", "
    Write-Warning "  PG '$($c.Name)' VLANs=[$vlans] Hosts=[$hostsList]"
  }
}

# Build unique list of PGs to create: (name + VLAN) but skip conflicted names
$conflictNames = @{}
foreach ($c in $conflicts) { $conflictNames[$c.Name] = $true }

$uniqueToCreate = $vssPgs |
  Where-Object { -not $conflictNames.ContainsKey($_.PgName) } |
  Group-Object PgName |
  ForEach-Object {
    # since no conflict, VLAN is unique
    $one = $_.Group | Select-Object -First 1
    [PSCustomObject]@{
      Name  = $one.PgName
      Vlan  = $one.VlanId
    }
  } |
  Sort-Object Name

Write-Host "Unique vSS port groups to copy: $($uniqueToCreate.Count)" -ForegroundColor Green

# Existing vDS PG names (avoid duplicates)
$existing = Get-VDPortgroup -VDSwitch $vds | Select-Object -ExpandProperty Name
$existingLookup = @{}
foreach ($n in $existing) { $existingLookup[$n] = $true }

# Create vDS port groups
foreach ($pg in $uniqueToCreate) {
  $pgName = $pg.Name
  $vlanId = [int]$pg.Vlan

  if ($existingLookup.ContainsKey($pgName)) {
    Write-Host "SKIP (exists on vDS): '$pgName'" -ForegroundColor Yellow
    continue
  }

  Write-Host "CREATE: '$pgName'  VLAN=$vlanId" -ForegroundColor Cyan

  try {
    if ($vlanId -eq 4095) {
      New-VDPortgroup -VDSwitch $vds -Name $pgName -NumPorts $NumPorts -VlanTrunkRange "0-4094" -ErrorAction Stop | Out-Null
      Write-Host "  -> created as TRUNK (0-4094)" -ForegroundColor Green
    } else {
      New-VDPortgroup -VDSwitch $vds -Name $pgName -NumPorts $NumPorts -VlanId $vlanId -ErrorAction Stop | Out-Null
      Write-Host "  -> created with VLAN ID $vlanId" -ForegroundColor Green
    }
  } catch {
    Write-Warning "FAILED to create '$pgName' (VLAN=$vlanId). Error: $($_.Exception.Message)"
  }
}

Write-Host "Done." -ForegroundColor Green
Disconnect-VIServer -Server $vCenter -Confirm:$false
