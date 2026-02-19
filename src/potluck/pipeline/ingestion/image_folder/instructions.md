# Image Folder Import

## Usage

Point Potluck at any folder containing images:

```bash
potluck ingest /path/to/photos --source image_folder
```

## Supported formats

**Images:** .jpg, .jpeg, .png, .gif, .webp, .heic, .heif, .bmp, .tiff, .tif, .svg, .raw, .cr2, .nef, .arw

## Notes

- EXIF dates are extracted from images for accurate timestamps
- Album names are inferred from subdirectory structure
- SHA256 hashes are computed for downstream deduplication
