<#
.SYNOPSIS
Read Windows Cloud Files placeholder sync state for explicitly supplied paths.
.DESCRIPTION
Uses CfGetPlaceholderInfo(CF_PLACEHOLDER_INFO_BASIC) with a READ_ATTRIBUTES
handle. It does not read file contents, hydrate files, or change sync state.
A folder's state does not prove its children are in sync; use -Recurse to
check each file in a copied archive folder, or supply the ZIP/PDF paths.
Exit code 0 means every checked item is a placeholder marked InSync by its
sync provider. Exit code 2 means at least one item is pending or unverified.
.EXAMPLE
& ./scripts/check_onedrive_sync.ps1 -Path 'C:\Users\ietsm\OneDrive\Playlist\archive.zip' -Json
.EXAMPLE
& ./scripts/check_onedrive_sync.ps1 -Path 'C:\Users\ietsm\OneDrive\Playlist' -Recurse -Json
.NOTES
Microsoft API references:
https://learn.microsoft.com/en-us/windows/win32/api/cfapi/nf-cfapi-cfgetplaceholderinfo
https://learn.microsoft.com/en-us/windows/win32/api/cfapi/ns-cfapi-cf_placeholder_basic_info
https://learn.microsoft.com/en-us/windows/win32/api/cfapi/ne-cfapi-cf_placeholder_info_class
https://learn.microsoft.com/en-us/windows/win32/api/cfapi/ne-cfapi-cf_in_sync_state
https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string[]] $Path,
    [switch] $Recurse,
    [switch] $Json
)

$ErrorActionPreference = 'Stop'
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw 'Windows Cloud Files API is available only on Windows.'
}

if (-not ('CodexCloudFiles.SyncReader' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

namespace CodexCloudFiles {
    public sealed class SyncResult {
        public string Path { get; set; }
        public bool ApiSucceeded { get; set; }
        public bool InSync { get; set; }
        public string InSyncState { get; set; }
        public int? PinState { get; set; }
        public string HResult { get; set; }
        public int? Win32Error { get; set; }
        public string Error { get; set; }
    }

    public static class SyncReader {
        private const uint FILE_READ_ATTRIBUTES = 0x00000080;
        private const uint FILE_SHARE_READ = 0x00000001;
        private const uint FILE_SHARE_WRITE = 0x00000002;
        private const uint FILE_SHARE_DELETE = 0x00000004;
        private const uint OPEN_EXISTING = 3;
        private const uint FILE_FLAG_BACKUP_SEMANTICS = 0x02000000;
        private const uint FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000;
        private const int CF_PLACEHOLDER_INFO_BASIC = 0;
        private const int CF_IN_SYNC_STATE_IN_SYNC = 1;
        private const uint HRESULT_MORE_DATA = 0x800700EA;

        [DllImport("kernel32.dll", EntryPoint = "CreateFileW", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern SafeFileHandle CreateFile(
            string name, uint access, uint share, IntPtr security,
            uint disposition, uint flags, IntPtr template);

        [DllImport("cldapi.dll", ExactSpelling = true)]
        private static extern int CfGetPlaceholderInfo(
            SafeFileHandle file, int infoClass, IntPtr buffer,
            uint bufferLength, out uint returnedLength);

        public static SyncResult Read(string path) {
            SyncResult result = new SyncResult {
                Path = System.IO.Path.GetFullPath(path), InSyncState = "Unknown"
            };
            try {
                string nativePath = result.Path.StartsWith(@"\\?\")
                    ? result.Path
                    : (result.Path.StartsWith(@"\\")
                        ? @"\\?\UNC\" + result.Path.Substring(2)
                        : @"\\?\" + result.Path);
                using (SafeFileHandle handle = CreateFile(nativePath, FILE_READ_ATTRIBUTES,
                    FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                    IntPtr.Zero, OPEN_EXISTING,
                    FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT, IntPtr.Zero)) {
                    if (handle.IsInvalid) {
                        int error = Marshal.GetLastWin32Error();
                        result.Win32Error = error;
                        result.Error = new System.ComponentModel.Win32Exception(error).Message;
                        return result;
                    }
                    // CF_PLACEHOLDER_BASIC_INFO begins with 32-bit PinState and
                    // InSyncState fields at offsets 0 and 4. The opaque provider
                    // FileIdentity field is never inspected or returned.
                    uint size = 4096;
                    while (size <= 1024 * 1024) {
                        IntPtr buffer = Marshal.AllocHGlobal((int)size);
                        try {
                            uint returned;
                            int hr = CfGetPlaceholderInfo(handle, CF_PLACEHOLDER_INFO_BASIC,
                                buffer, size, out returned);
                            result.HResult = "0x" + unchecked((uint)hr).ToString("X8");
                            if (hr == 0 && returned >= 8) {
                                int state = Marshal.ReadInt32(buffer, 4);
                                result.ApiSucceeded = true;
                                result.PinState = Marshal.ReadInt32(buffer, 0);
                                result.InSync = state == CF_IN_SYNC_STATE_IN_SYNC;
                                result.InSyncState = state == 1 ? "InSync" : state == 0 ? "NotInSync" : "Unknown(" + state + ")";
                                return result;
                            }
                            if (unchecked((uint)hr) != HRESULT_MORE_DATA) {
                                result.Error = hr == 0 ? "Cloud Files API returned incomplete basic information."
                                    : Marshal.GetExceptionForHR(hr).Message;
                                return result;
                            }
                            size = Math.Max(size * 2, returned);
                        } finally {
                            Marshal.FreeHGlobal(buffer);
                        }
                    }
                    result.Error = "Cloud Files API information exceeded the bounded metadata buffer.";
                }
            } catch (Exception error) {
                result.Error = error.Message;
            }
            return result;
        }
    }
}
'@
}

$taskItems = [System.Collections.Generic.List[string]]::new()
foreach ($taskInput in $Path) {
    $taskItem = Get-Item -LiteralPath $taskInput -Force
    if ($taskItem.PSIsContainer -and $Recurse) {
        foreach ($taskChild in (Get-ChildItem -LiteralPath $taskItem.FullName -File -Force -Recurse)) {
            $taskItems.Add($taskChild.FullName)
        }
    } else {
        $taskItems.Add($taskItem.FullName)
    }
}

$taskResults = @($taskItems | Select-Object -Unique | ForEach-Object { [CodexCloudFiles.SyncReader]::Read($_) })
$taskUnverified = @($taskResults | Where-Object { -not $_.ApiSucceeded -or -not $_.InSync })
$taskReport = [pscustomobject]@{
    checked_at = [DateTimeOffset]::UtcNow.ToString('o')
    method = 'CfGetPlaceholderInfo(CF_PLACEHOLDER_INFO_BASIC), READ_ATTRIBUTES only'
    all_in_sync = ($taskResults.Count -gt 0 -and $taskUnverified.Count -eq 0)
    checked_item_count = $taskResults.Count
    in_sync_count = @($taskResults | Where-Object { $_.ApiSucceeded -and $_.InSync }).Count
    unverified_count = $taskUnverified.Count
    results = $taskResults
}
if ($Json) {
    $taskReport | ConvertTo-Json -Depth 6
} else {
    $taskReport
}
if (-not $taskReport.all_in_sync) {
    exit 2
}
