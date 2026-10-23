import os

# Refuse to decode absurdly large images (decompression bombs) before cv2 is imported anywhere.
os.environ.setdefault("OPENCV_IO_MAX_IMAGE_PIXELS", os.getenv("CLEARWAY_MAX_IMAGE_PIXELS", str(4096 * 4096)))
