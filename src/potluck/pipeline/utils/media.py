"""Shared media file extension constants used across ingesters."""

IMAGE_EXTENSIONS = frozenset(
    {
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".webp",
        ".heic",
        ".heif",
        ".bmp",
        ".tiff",
        ".tif",
        ".raw",
        ".cr2",
        ".nef",
        ".arw",
        ".svg",
    }
)

VIDEO_EXTENSIONS = frozenset(
    {
        ".mp4",
        ".mov",
        ".avi",
        ".mkv",
        ".webm",
        ".flv",
        ".wmv",
        ".m4v",
        ".3gp",
        ".mpg",
        ".mpeg",
    }
)

AUDIO_EXTENSIONS = frozenset(
    {
        ".mp3",
        ".wav",
        ".flac",
        ".aac",
        ".ogg",
        ".m4a",
        ".wma",
    }
)

ALL_MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS | AUDIO_EXTENSIONS
PHOTO_VIDEO_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS
