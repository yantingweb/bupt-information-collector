<#
  注册 Windows 计划任务：每天 07:30 / 12:30 / 19:00 扫一遍并推手机。
  用管理员 PowerShell 跑：  .\deploy\Install.ps1
  卸载：                    .\deploy\Install.ps1 -Uninstall

  两个坑（都踩过，别再改回去）：
    1. 计划任务名不能含冒号 —— "07:30" 会被拒（0x80070057），所以名字里去掉冒号。
    2. 注册完必须回查一遍。以前不回查，脚本每次都打印「已注册」，实际一条都没装上。
#>
param([switch]$Uninstall)

$TaskName = "bupt-info-collector"
$Project  = Split-Path -Parent $PSScriptRoot
$Python   = (Get-Command python -ErrorAction Stop).Source

if ($Uninstall) {
    Get-ScheduledTask -TaskName "$TaskName*" -ErrorAction SilentlyContinue |
        Unregister-ScheduledTask -Confirm:$false
    Write-Host "已卸载 $TaskName" -ForegroundColor Yellow
    exit 0
}

# -X utf8：日志里有中文，不加会在计划任务里跑成乱码
$Jobs = @(
    @{ t = "07:30"; a = "scan --push"; d = "早上：扫论坛/门户并推手机" }
    @{ t = "12:30"; a = "scan --push"; d = "中午补扫" }
    @{ t = "19:00"; a = "scan --push"; d = "晚上收最后一次" }
)

foreach ($j in $Jobs) {
    $name = "$TaskName " + ($j.t -replace ":", "")
    $act  = New-ScheduledTaskAction -Execute $Python `
        -Argument "-X utf8 `"$Project\run.py`" $($j.a)" -WorkingDirectory $Project
    $trg  = New-ScheduledTaskTrigger -Daily -At $j.t
    $set  = New-ScheduledTaskSettingsSet -StartWhenAvailable `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 20) `
        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 10)

    Register-ScheduledTask -TaskName $name -Action $act -Trigger $trg `
        -Settings $set -Description $j.d -Force | Out-Null

    if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
        Write-Host "  OK   $name" -ForegroundColor Green
    } else {
        Write-Host "  FAIL $name 注册失败" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "完成。立刻试跑一次：" -ForegroundColor Cyan
Write-Host "  Start-ScheduledTask -TaskName '$TaskName 0730'"
Write-Host "  Get-ScheduledTaskInfo -TaskName '$TaskName 0730' | Select LastRunTime,LastTaskResult"
Write-Host ""
Write-Host "注意：合盖休眠的机器上任务不会自己醒 —— 唤醒要额外配 WakeToRun。" -ForegroundColor DarkGray
