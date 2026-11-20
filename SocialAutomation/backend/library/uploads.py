"""Strict checks for uploaded video containers and safe storage paths."""
import uuid
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError


VIDEO_TYPES = {
    '.mp4': ('video/mp4', 'mp4'),
    '.m4v': ('video/mp4', 'mp4'),
    '.mov': ('video/quicktime', 'quicktime'),
    '.webm': ('video/webm', 'ebml'),
    '.mkv': ('video/x-matroska', 'ebml'),
    '.avi': ('video/x-msvideo', 'avi'),
}


def validate_video_upload(upload):
    """Check the size, allowed extension, and a container signature from bytes."""
    name = upload.name or ''
    extension = Path(name.replace('\\', '/')).suffix.lower()
    if extension not in VIDEO_TYPES:
        raise ValidationError('Upload an MP4, M4V, MOV, WebM, MKV, or AVI video.')
    if upload.size <= 0:
        raise ValidationError('The video file is empty.')
    if upload.size > settings.MEDIA_MAX_UPLOAD_BYTES:
        max_mb = settings.MEDIA_MAX_UPLOAD_BYTES // (1024 * 1024)
        raise ValidationError(f'The video is larger than the {max_mb} MB upload limit.')

    header = upload.read(4096)
    upload.seek(0)
    _mime_type, expected = VIDEO_TYPES[extension]
    valid = False
    if expected == 'mp4':
        valid = len(header) >= 12 and header[4:8] == b'ftyp'
    elif expected == 'quicktime':
        valid = len(header) >= 12 and header[4:8] == b'ftyp' and header[8:12] == b'qt  '
    elif expected == 'ebml':
        valid = header.startswith(b'\x1a\x45\xdf\xa3') and (
            (extension == '.webm' and b'\x42\x82\x84webm' in header)
            or (extension == '.mkv' and b'\x42\x82\x88matroska' in header)
        )
    elif expected == 'avi':
        valid = len(header) >= 12 and header[:4] == b'RIFF' and header[8:12] == b'AVI '
    if not valid:
        raise ValidationError('The file contents do not match a supported video format.')
    return extension, _mime_type


def private_video_upload_path(instance, original_name):
    """Ignore the client filename and make a random path under the owner's folder."""
    extension = Path((original_name or '').replace('\\', '/')).suffix.lower()
    if extension not in VIDEO_TYPES:
        extension = '.bin'
    return f'private-media/user-{instance.owner_id}/{uuid.uuid4().hex}{extension}'
