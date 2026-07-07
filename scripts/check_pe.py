import struct

with open(r'F:\GitHubDesktop\GitHubDesktopSetup.exe', 'rb') as f:
    f.seek(0x3C)
    pe_offset = struct.unpack('<I', f.read(4))[0]
    f.seek(pe_offset)
    sig = f.read(4)
    machine = struct.unpack('<H', f.read(2))[0]

machines = {
    0x014c: 'x86 (32-bit)',
    0x8664: 'x64 (AMD64)',
    0xAA64: 'ARM64',
    0x01c4: 'ARMv7 (32-bit)',
}
arch = machines.get(machine, 'Unknown')
print(f'PE offset: {pe_offset}')
print(f'Signature: {sig}')
print(f'Machine: 0x{machine:04X} - {arch}')
import os
fsize = os.path.getsize(r'F:\GitHubDesktop\GitHubDesktopSetup.exe')
print(f'File size: {fsize} bytes ({fsize/1024/1024:.1f} MB)')
