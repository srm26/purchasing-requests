"""File storage: Azure Blob (managed identity) in Azure, a local folder for local development."""
from functools import lru_cache
from pathlib import Path

from ..config import get_settings

OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # legacy Office (.doc/.xls)
ZIP = b"PK\x03\x04"  # Office Open XML (.docx/.xlsx)
# Allowed attachment types: extension -> (content type, file signature the bytes must start with)
FILE_TYPES = {
    ".pdf": ("application/pdf", b"%PDF-"),
    ".doc": ("application/msword", OLE),
    ".docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ZIP),
    ".xls": ("application/vnd.ms-excel", OLE),
    ".xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ZIP),
}


def extension(name: str) -> str:
    return Path(name).suffix.lower()


def content_type(name: str) -> str:
    return FILE_TYPES.get(extension(name), ("application/octet-stream", b""))[0]


@lru_cache
def _blob_service():
    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import BlobServiceClient

    return BlobServiceClient(get_settings().blob_account_url, credential=DefaultAzureCredential())


def _local_path(container: str, name: str) -> Path:
    root = Path(get_settings().local_files_dir).resolve()
    path = (root / container / name).resolve()
    if root not in path.parents:
        raise ValueError("Invalid file path")
    return path


def put(container: str, name: str, data: bytes, content_type: str = "application/octet-stream") -> None:
    if get_settings().blob_account_url:
        from azure.storage.blob import ContentSettings

        _blob_service().get_blob_client(container, name).upload_blob(
            data, overwrite=True, content_settings=ContentSettings(content_type=content_type)
        )
        return
    path = _local_path(container, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def get(container: str, name: str) -> bytes:
    if get_settings().blob_account_url:
        return _blob_service().get_blob_client(container, name).download_blob().readall()
    return _local_path(container, name).read_bytes()


def copy_local_to_blob() -> int:
    """Moving to Blob: upload files saved on local disk before Blob was turned on, skipping ones already
    in Blob. Local copies are kept. Returns how many files were uploaded."""
    s = get_settings()
    root = Path(s.local_files_dir)
    if not s.blob_account_url or not root.is_dir():
        return 0
    from azure.storage.blob import ContentSettings

    uploaded = 0
    for container in (s.blob_attachments_container, s.blob_backups_container):
        for path in sorted((root / container).rglob("*")):
            if not path.is_file():
                continue
            blob = _blob_service().get_blob_client(container, path.relative_to(root / container).as_posix())
            if blob.exists():
                continue
            blob.upload_blob(path.read_bytes(), content_settings=ContentSettings(content_type=content_type(path.name)))
            uploaded += 1
    return uploaded
