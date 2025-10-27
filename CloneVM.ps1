################################################
# Script for make clone of vmware esxi VMs
# vm_list1.csv is file with VM names, one in row
# it can work as "backup" solution.
################################################

Import-Module VMware.PowerCLI

# Define variables
$vmNamesFile = "vm_list.csv"
# Define the dynamic prefix based on the current date
$prefix = "Clone-" + (Get-Date -Format "yy-MM-dd") + "-"
$folderName = "OfflineBackup"
$datastoreName = "STORE_Clone"
$esxClone = Get-VMHost -Name "esx-backup.win.local.domain"

# Connect to vCenter Server
$vcServer = "vcenter.win.local.domain"
Connect-VIServer -Server $vcServer -User "win.local.domain" -Password "S3cur3P@ssW0rd!"

# Get the target datastore
$datastore = Get-Datastore -Name $datastoreName

# Get the target folder
$folder = Get-Folder -Name $folderName

# Get the virtual machines to clone
$vms = Get-Content $vmNamesFile | ForEach-Object { Get-VM -Name $_ }

# Clone each VM to the ESX
$taskResults = @()
foreach ($vm in $vms) {
    $newVmName = $prefix + $vm.Name
    $cloneTask = $vm | New-VM -Name $newVmName -VMHost $esxClone -Datastore $datastore -Location $folder -RunAsync
    $taskResults += $cloneTask | Wait-Task
}

# Power off each cloned VM
foreach ($taskResult in $taskResults) {
    $clonedVm = Get-View $taskResult.Info.Result
    $clonedVm | Get-VM | Stop-VM -Confirm:$false
}
