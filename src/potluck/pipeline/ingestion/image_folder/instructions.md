# Image / Media Folder Import

## Usage

Point Potluck at any folder containing images, videos, or audio:

```bash
potluck ingest /path/to/photos --source image_folder
```

## Supported formats

**Images:** .jpg, .jpeg, .png, .gif, .webp, .heic, .heif, .bmp, .tiff, .tif, .svg, .raw, .cr2, .nef, .arw
**Video:** .mp4, .mov, .avi, .mkv, .webm, .flv, .wmv, .m4v, .3gp, .mpg, .mpeg
**Audio:** .mp3, .wav, .flac, .aac, .ogg, .m4a, .wma

## Notes

- EXIF dates are extracted from images for accurate timestamps
- Album names are inferred from subdirectory structure
- Files are deduplicated by SHA256 hash
