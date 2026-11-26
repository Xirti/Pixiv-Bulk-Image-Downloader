"""Application identity; preserve its original storage and login namespace."""
from version import __version__

PROJECT_AUTHOR = "Xirti"
COPYRIGHT_NAME = "Aperia"
PROJECT_REPOSITORY = "Xirti/Pixiv-Bulk-Image-Downloader"
PROJECT_URL = "https://github.com/" + PROJECT_REPOSITORY
RELEASE_TAG = f"v{__version__}"
DATA_FOLDER = "MOKU"
CREDENTIAL_TARGET = "MOKU.Pixiv.PHPSESSID"
MUTEX_NAME = r"Local\MOKU.PixivTagGallery.Backend.v1"
