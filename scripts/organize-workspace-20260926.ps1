$ErrorActionPreference = 'Stop'
$workspace = (Resolve-Path -LiteralPath 'F:\vps').Path.TrimEnd('\')
$auditDir = Join-Path $workspace 'docs\reports\整理-20260926'
New-Item -ItemType Directory -Path $auditDir -Force | Out-Null
$moves = @{
 '交接文档-总-20260913.md'='docs/archive/20260913/交接文档-总-20260913.md'
 '路由器集群项目计划.md'='docs/archive/20260913/路由器集群项目计划.md'
 '路由器刷机交接文档.md'='docs/archive/20260913/路由器刷机交接文档.md'
 '刷机任务交接书-千兆电脑执行版.md'='docs/archive/20260913/刷机任务交接书-千兆电脑执行版.md'
 '刷机现场交接-20260914.md'='docs/archive/20260914/刷机现场交接-20260914.md'
 'docs/AI交接文档.md'='docs/archive/20260906/AI交接文档.md'
 'docs/VPS交接文档.md'='docs/archive/20260906/VPS交接文档.md'
 'docs/竞斗云刷机交接-20260921.md'='docs/archive/r619ac/竞斗云刷机交接-20260921.md'
 '固件-r619ac'='docs/archive/r619ac/固件-r619ac'
 '路由器管理系统-开发执行计划.md'='docs/reference/路由器管理系统-开发执行计划.md'
 '路由器代理配置交接-20260917.md'='docs/operations/路由器代理配置交接-20260917.md'
 '管理台远程策略与刷机补充-20260920.md'='docs/operations/管理台远程策略与刷机补充-20260920.md'
 '刷机发货流程SOP-雅典娜AX6600-20260917.md'='docs/operations/刷机发货流程SOP-雅典娜AX6600-20260917.md'
 'SN-R1-AX3000-记录与刷机准备-20260923.md'='docs/operations/SN-R1-AX3000-记录与刷机准备-20260923.md'
 'docs/服务器访问通道总览-20260923.md'='docs/operations/服务器访问通道总览-20260923.md'
 '阶段0执行报告-20260924.md'='docs/reports/阶段0执行报告-20260924.md'
 '阶段1执行报告-20260925.md'='docs/reports/阶段1执行报告-20260925.md'
 'docs/变更记录-20260925-腾讯第二节点上线与双线容灾.md'='docs/reports/变更记录-20260925-腾讯第二节点上线与双线容灾.md'
 'VPS与路由器系统检查及完善执行计划-20260924.md'='docs/reference/VPS与路由器系统检查及完善执行计划-20260924.md'
}
$manifest = @()
foreach ($entry in $moves.GetEnumerator() | Sort-Object Name) {
 $source = [IO.Path]::GetFullPath((Join-Path $workspace $entry.Key))
 $target = [IO.Path]::GetFullPath((Join-Path $workspace $entry.Value))
 foreach ($candidate in @($source,$target)) {
  if (-not $candidate.StartsWith($workspace + '\', [StringComparison]::OrdinalIgnoreCase)) { throw '路径超出工作目录' }
 }
 if (-not (Test-Path -LiteralPath $source)) { throw "源不存在: $source" }
 if (Test-Path -LiteralPath $target) { throw "目标已存在: $target" }
 $item = Get-Item -LiteralPath $source
 if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw '不移动链接目录' }
 $files = if ($item.PSIsContainer) { @(Get-ChildItem -LiteralPath $source -File -Recurse) } else { @($item) }
 foreach ($file in $files) {
  $suffix = if ($item.PSIsContainer) { $file.FullName.Substring($source.Length) } else { '' }
  $manifest += [pscustomobject]@{ OldPath=$file.FullName; NewPath=$target+$suffix; SHA256=(Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash }
 }
}
$manifest | Export-Csv -LiteralPath (Join-Path $auditDir '迁移清单.csv') -NoTypeInformation -Encoding utf8
# Save editable entry documents before revising them. No production files are executed.
foreach ($relative in @('README.md','mgmt/README.md','mgmt/docs/开发进度.md','docs/AI分析历史会话指南.md')) {
 $dest = Join-Path $auditDir ('原件/' + $relative)
 New-Item -ItemType Directory -Path (Split-Path -Parent $dest) -Force | Out-Null
 Copy-Item -LiteralPath (Join-Path $workspace $relative) -Destination $dest
}
foreach ($entry in $moves.GetEnumerator() | Sort-Object Name) {
 $source = [IO.Path]::GetFullPath((Join-Path $workspace $entry.Key))
 $target = [IO.Path]::GetFullPath((Join-Path $workspace $entry.Value))
 New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
 Move-Item -LiteralPath $source -Destination $target
}
foreach ($row in $manifest) {
 if ((Get-FileHash -LiteralPath $row.NewPath -Algorithm SHA256).Hash -ne $row.SHA256) { throw "校验失败: $($row.NewPath)" }
}
Write-Output "已迁移并校验 $($manifest.Count) 个文件；原件和清单: $auditDir"
