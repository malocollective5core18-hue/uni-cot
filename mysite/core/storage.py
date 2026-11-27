"""Cloudinary storage backends used by UNI-COT."""
import time
from pathlib import PurePosixPath
from uuid import uuid4

import cloudinary
import cloudinary.uploader
import cloudinary.utils
from cloudinary import CloudinaryImage
from cloudinary_storage.storage import MediaCloudinaryStorage, RawMediaCloudinaryStorage, StaticCloudinaryStorage
from django.utils.deconstruct import deconstructible


class CloudinaryMappedStaticStorage(StaticCloudinaryStorage):
    """Static storage that maps local paths to Cloudinary public_ids"""
    
    # Mapping from relative path to Cloudinary public_id (without the ring0 prefix and with correct case)
    PATH_MAP = {
        'ringui/b1.png': 'ring0/static/ringui/B1',
        'ringui/b2.png': 'ring0/static/ringui/B2',
        'ringui/b3.png': 'ring0/static/ringui/B3_compressed',
        'ringui/b4.png': 'ring0/static/ringui/B4',
        'ringui/b5.png': 'ring0/static/ringui/B5',
        'ringui/b6.png': 'ring0/static/ringui/B6',
        'ringui/d1.png': 'ring0/static/ringui/D1',
        'ringui/d2.png': 'ring0/static/ringui/D2',
        'ringui/d3.png': 'ring0/static/ringui/D3',
        'ringui/d4.png': 'ring0/static/ringui/D4',
        'ringui/d5.png': 'ring0/static/ringui/D5',
        'ringui/d6.png': 'ring0/static/ringui/D6',
        'company/ceo.jpg': 'ring0/static/company/CEO',
        'company/cot.png': 'ring0/static/company/cot',
        'company/uni.png': 'ring0/static/company/uni',
        'company/uni-cot.png': 'ring0/static/company/UNI-COT',
        'company/ring0.png': 'ring0/static/company/ring0',
        'ringui/company/ceo.jpg': 'ring0/static/company/CEO',
        'ringui/company/cot.png': 'ring0/static/company/cot',
        'ringui/company/uni.png': 'ring0/static/company/uni',
        'ringui/company/uni-cot.png': 'ring0/static/company/UNI-COT',
        'ringui/company/ring0.png': 'ring0/static/company/ring0',
        'service/images/ringui/b1.png': 'ring0/service/ringui/B1',
        'service/images/ringui/b2.png': 'ring0/service/ringui/B2',
        'service/images/ringui/b3.png': 'ring0/service/ringui/B3_compressed',
        'service/images/ringui/b4.png': 'ring0/service/ringui/B4',
        'service/images/ringui/b5.png': 'ring0/service/ringui/B5',
        'service/images/ringui/b6.png': 'ring0/service/ringui/B6',
        'service/images/ringui/d1.png': 'ring0/service/ringui/D1',
        'service/images/ringui/d2.png': 'ring0/service/ringui/D2',
        'service/images/ringui/d3.png': 'ring0/service/ringui/D3',
        'service/images/ringui/d4.png': 'ring0/service/ringui/D4',
        'service/images/ringui/d5.png': 'ring0/service/ringui/D5',
        'service/images/ringui/d6.png': 'ring0/service/ringui/D6',
    }
    
    def url(self, name):
        """Return Cloudinary URL for the given file name"""
        path = name.lower()
        
        # Check if we have a mapping
        if path in self.PATH_MAP:
            public_id = self.PATH_MAP[path]
            return cloudinary.CloudinaryImage(public_id).build_url()
        
        # Fallback to parent implementation
        return super().url(name)


@deconstructible
class PrivateWorkbookCloudinaryStorage(RawMediaCloudinaryStorage):
    """Store workbook sources as authenticated raw files and never expose a CDN URL.

    Existing keys from the default image storage remain readable/deletable while
    they are migrated. New keys live in a distinct folder so the delivery mode
    can be identified from the FileField name.
    """

    PRIVATE_FOLDER = 'private_workbooks'

    @staticmethod
    def is_ready():
        config = cloudinary.config()
        return bool(config.cloud_name and config.api_key and config.api_secret)

    def get_available_name(self, name, max_length=None):
        # _save adds a UUID to the Cloudinary public ID, so a remote HEAD request
        # is unnecessary and would be unreliable for authenticated assets.
        return name

    @classmethod
    def is_private_workbook(cls, name):
        path = '/' + str(name or '').replace('\\', '/').lstrip('/')
        return f'/{cls.PRIVATE_FOLDER}/' in path

    @staticmethod
    def _private_asset_id(name):
        return str(name or '').replace('\\', '/').lstrip('/')

    @classmethod
    def _private_asset_parts(cls, name):
        public_id = cls._private_asset_id(name)
        extension = PurePosixPath(public_id).suffix.lstrip('.').lower()
        if not extension:
            raise ValueError('Private workbook storage key has no file extension.')
        return public_id, extension

    def _save(self, name, content):
        filename = PurePosixPath(name.replace('\\', '/')).name
        if not filename or not PurePosixPath(filename).suffix:
            raise ValueError('Workbook storage requires a file extension.')
        public_id = self._prepend_prefix(
            f'{self.PRIVATE_FOLDER}/{uuid4().hex}/{filename}'
        )
        result = cloudinary.uploader.upload(
            content,
            public_id=public_id,
            resource_type='raw',
            type='authenticated',
            overwrite=False,
            use_filename=False,
            unique_filename=False,
            tags=self.TAG,
        )
        stored_id = result.get('public_id')
        if not stored_id:
            raise IOError('Cloudinary did not return a workbook asset identifier.')
        extension = PurePosixPath(filename).suffix.lower()
        if not PurePosixPath(stored_id).suffix:
            stored_id += extension
        return stored_id

    def _get_url(self, name):
        if not self.is_private_workbook(name):
            # Compatibility for existing assets that were stored before the
            # field was assigned this backend. A migration command can read
            # these through the old image delivery path and re-upload them.
            return MediaCloudinaryStorage()._get_url(name)
        public_id, extension = self._private_asset_parts(name)
        return cloudinary.utils.private_download_url(
            public_id,
            extension,
            resource_type='raw',
            type='authenticated',
            expires_at=int(time.time()) + 300,
            attachment=True,
        )

    def url(self, name):
        raise ValueError('Workbook source URLs are available only through an authorized application view.')

    def delete(self, name):
        if not self.is_private_workbook(name):
            return MediaCloudinaryStorage().delete(name)
        public_id, _extension = self._private_asset_parts(name)
        result = cloudinary.uploader.destroy(
            public_id,
            resource_type='raw',
            type='authenticated',
            invalidate=True,
        )
        return result.get('result') == 'ok'
