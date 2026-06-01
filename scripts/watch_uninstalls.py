"""Poll the Windows registry for our 6 published apps and emit one line
when each disappears, plus a final ``all_uninstalled`` when none remain.

Designed to be the command body of a Monitor task -- each stdout line
becomes a notification. Polling cadence 30s.
"""

import subprocess
import sys
import time

# Map of display name -> Uninstall key suffix (ProductCode GUID for MSIs,
# bare key name for the EXE/NSIS case).
APPS = {
    'KeePass':   r'{33F0E718-1110-4F95-A59B-230A62C02DE5}',
    'Node.js':   r'{8E3EF5A2-585E-453B-B16C-B46E05A62DAC}',
    'Slack':     r'{D0275306-75E4-4E2E-ABF1-32C784A90E32}',
    'Zoom':      r'{3DC6B3F0-0AD7-4B51-AFA2-59605590EAC5}',
    'Webex':     r'{01506053-C4C5-5E0F-87A7-ED844972D6D8}',
    'Notepad++': r'Notepad++',
}

UNINSTALL_KEY_BASE = r'HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall'


def is_installed(suffix: str) -> bool:
    r = subprocess.run(
        ['reg', 'query', f'{UNINSTALL_KEY_BASE}\\{suffix}'],
        capture_output=True, text=True,
    )
    return ('DisplayVersion' in r.stdout) or ('DisplayName' in r.stdout)


def main():
    previous = set(APPS)
    print('start: ' + ', '.join(sorted(previous)), flush=True)
    while True:
        current = {name for name, suffix in APPS.items() if is_installed(suffix)}
        if current != previous:
            for removed in sorted(previous - current):
                print(f'uninstalled: {removed}', flush=True)
            previous = current
        if not current:
            print('all_uninstalled', flush=True)
            return
        time.sleep(30)


if __name__ == '__main__':
    sys.exit(main())
