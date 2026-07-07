"""Read GitHub credential from Windows Credential Manager and use it with gh CLI"""
import subprocess
import sys
import os
import json

# First, try to get the credential using PowerShell
ps_cmd = '''
Add-Type -AssemblyName System.Security
$cred = [System.Net.CredentialCache]::DefaultCredentials
$target = "GitHub - https://api.github.com/915698157yssss"

# Try CredWrite/CredRead
$credManager = @"
using System;
using System.Runtime.InteropServices;
using System.Text;

public class CredManager {
    public static string GetCredential(string target) {
        IntPtr credPtr;
        bool result = CredRead(target, 1, 0, out credPtr);
        if (!result) return null;
        
        var cred = (CREDENTIAL)Marshal.PtrToStructure(credPtr, typeof(CREDENTIAL));
        string password = Marshal.PtrToStringUni(cred.CredentialBlob, cred.CredentialBlobSize / 2);
        CredFree(credPtr);
        
        return password;
    }
    
    [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    private static extern bool CredRead(string target, uint type, uint flags, out IntPtr credential);
    
    [DllImport("advapi32.dll", SetLastError = true)]
    private static extern void CredFree(IntPtr cred);
    
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct CREDENTIAL {
        public uint Flags;
        public uint Type;
        public string TargetName;
        public string Comment;
        public long LastWritten;
        public uint CredentialBlobSize;
        public IntPtr CredentialBlob;
        public uint Persist;
        public uint AttributeCount;
        public IntPtr Attributes;
        public string TargetAlias;
        public string UserName;
    }
}
"@

Add-Type -TypeDefinition $credManager
$token = [CredManager]::GetCredential("GitHub - https://api.github.com/915698157yssss")
if ($token) {
    Write-Output "TOKEN_FOUND"
    Write-Output $token
} else {
    Write-Output "NO_TOKEN"
}
'''

# Run PowerShell to get token
result = subprocess.run(['powershell', '-Command', ps_cmd], capture_output=True, text=True, timeout=15)
output = result.stdout.strip()

if 'TOKEN_FOUND' in output:
    lines = output.split('\n')
    # Find the TOKEN_FOUND line and get the token after it
    idx = next(i for i, l in enumerate(lines) if 'TOKEN_FOUND' in l)
    token = lines[idx + 1].strip()
    print(f'Token found! Length: {len(token)}')
    
    # Use token with gh CLI
    gh_path = r'C:\Program Files\GitHub CLI\gh.exe'
    
    # Login with token
    auth_result = subprocess.run(
        [gh_path, 'auth', 'login', '--with-token'],
        input=token, capture_output=True, text=True, timeout=15
    )
    print(f'Auth: {auth_result.stdout.strip()}')
    if auth_result.stderr:
        print(f'Auth err: {auth_result.stderr.strip()}')
    
    # Create repo and push
    repo_result = subprocess.run(
        [gh_path, 'repo', 'create', 'SmartFileOrganizer', 
         '--private', '--description', '智能文件分类器',
         '--push', '--source', r'F:\SmartFileOrganizer', '--remote', 'origin'],
        capture_output=True, text=True, timeout=60
    )
    print(f'Create repo: {repo_result.stdout.strip()}')
    if repo_result.stderr:
        print(f'Stderr: {repo_result.stderr.strip()}')
        
else:
    print('No token found in credential manager')
    print(output)
