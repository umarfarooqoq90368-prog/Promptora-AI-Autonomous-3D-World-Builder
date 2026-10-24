import glob
import os
from pathlib import Path

from django.conf import settings

# Typical folder created by the Blender Windows installer / winget.
COMMON_ROOTS = [
    r'C:\Program Files\Blender Foundation',
    r'C:\Program Files (x86)\Blender Foundation',
    os.path.expandvars(r'%LOCALAPPDATA%\Programs\Blender Foundation'),
    r'D:\Blender',
]

SETUP_INSTRUCTIONS = (
    'Blender executable not found. To fix this:\n'
    '1) Install Blender: run "winget install BlenderFoundation.Blender" '
    '(or download from https://www.blender.org/download).\n'
    '2) If you installed it to a custom folder, copy the full blender.exe '
    'path into .env as BLENDER_EXE=... Example:\n'
    '   BLENDER_EXE=C:\\Program Files\\Blender Foundation\\Blender 4.5\\blender.exe\n'
    '3) Restart the Django server.'
)


def find_blender():
    """Locate a usable blender.exe. Returns (path, '') on success, or (None, setup_message)."""
    configured = getattr(settings, 'BLENDER_EXE', '')
    if configured:
        candidate = Path(configured)
        if candidate.is_file():
            return str(candidate), ''
        return None, (
            'The configured BLENDER_EXE does not exist: %s\n%s' % (configured, SETUP_INSTRUCTIONS)
        )

    for root in COMMON_ROOTS:
        matches = sorted(glob.glob(os.path.join(root, '*', 'blender.exe')), reverse=True)
        if matches:
            return matches[0], ''

    return None, SETUP_INSTRUCTIONS
