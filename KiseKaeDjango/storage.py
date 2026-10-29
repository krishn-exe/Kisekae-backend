import os
from storages.backends.s3 import S3Storage
from storages.utils import clean_name


class KiseKaeS3Storage(S3Storage):
    """
    Unified S3 Storage backend aligned with the S3 Bucket Policy:
    
    1. Public Assets ('products/*', 'stores/*'):
       Explicitly allowed by the bucket policy 'PublicReadProductsAndStores'.
       Served with permanent, clean public URLs without query string parameters
       so they can be cached by CDNs and browsers.
       
    2. Private Assets ('tryon/*', etc.):
       Customer photos (selfies/body pictures) and AI try-on generation results
       are kept strictly private (not in the public bucket policy).
       Served as AWS SigV4 presigned URLs with temporary expiration to prevent
       unauthorized access while avoiding 403 Forbidden errors.
    """
    PUBLIC_PREFIXES = ("products/", "stores/")

    def url(self, name, parameters=None, expire=None, http_method=None):
        name = self._normalize_name(clean_name(name))

        if any(name.startswith(prefix) for prefix in self.PUBLIC_PREFIXES):
            if self.custom_domain:
                return f"{self.url_protocol}//{self.custom_domain}/{name}"
            return f"https://{self.bucket_name}.s3.{self.region_name}.amazonaws.com/{name}"

        return super().url(name, parameters=parameters, expire=expire, http_method=http_method)
