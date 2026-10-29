"""Which paths are cloud-synced folders. Shared by the Drive guard and the video helpers.

On macOS and Windows alike, Google Drive, Dropbox, OneDrive and iCloud Drive keep files as on-demand
placeholders: a media tool that opens one either stalls or pulls the whole file down first, and writing a
render into the folder starts an upload while the render is still running. Standard library only."""
from __future__ import annotations

import re

# The same patterns as the shell kit this replaces, plus the Windows spellings (the iCloudDrive folder).
_CLOUD = re.compile(
    r"CloudStorage|Mobile.*Documents|CloudDocs|Volumes/GoogleDrive|iCloudDrive"
    r"|Google.*Drive|My.*Drive|Shared.*drives|Dropbox|OneDrive|Мой.*диск|Мій.*диск", re.S)


def mentions_cloud(text):
    """True when the text names a cloud-synced folder (a path, or a whole command line)."""
    return bool(text) and _CLOUD.search(str(text)) is not None
