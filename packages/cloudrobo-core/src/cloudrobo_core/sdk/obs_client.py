import os
import logging
from typing import Any, Dict, Optional
from rich.progress import Progress, BarColumn, TextColumn, TaskProgressColumn, TimeRemainingColumn

try:
    from obs import ObsClient
except ImportError as ex:
    raise ImportError("Please install esdk-obs-python SDK: pip install esdk-obs-python") from ex

from cloudrobo_core.sdk.config import Config

logger = logging.getLogger(__name__)

_OBS_ENDPOINT_ENV_KEY = "CLOUDROBO_OBS_ENDPOINT"


class OBSClient:

    def __init__(self, config: Optional[Config] = None, bucket_name: str = None):
        """
        Initialize OBS client

        Args:
            config: Config instance, creates a default one if not provided
            bucket_name: Default bucket name
        """
        self.config = config or Config()
        self.bucket_name = bucket_name

        self.access_key = self.config.ak
        self.secret_key = self.config.sk
        self.endpoint = self._resolve_endpoint()

        # Create OBS client instance
        try:
            self.client = ObsClient(
                access_key_id=self.access_key,
                secret_access_key=self.secret_key,
                server=self.endpoint
            )
            logger.info("OBS client initialized successfully")
        except Exception as e:
            logger.error(f"OBS client initialization failed: {e}")
            raise

    def _resolve_endpoint(self) -> str:
        """Resolve OBS endpoint from environment variable or config."""
        endpoint = os.environ.get(_OBS_ENDPOINT_ENV_KEY, "")
        if endpoint:
            return endpoint
        endpoint = self.config.endpoints.get("cloudrobo-obs", "")
        if "{region}" in endpoint:
            endpoint = endpoint.replace("{region}", self.config.region)
        return endpoint

    def set_bucket(self, bucket_name: str):
        """
        Set default bucket
        
        Args:
            bucket_name: Bucket name
        """
        self.bucket_name = bucket_name

    def create_bucket(self, bucket_name: str = None) -> bool:
        """
        Create bucket

        Args:
            bucket_name: Bucket name, use default bucket if not specified

        Returns:
            Return True if creation succeeds, otherwise False
        """
        if bucket_name is None:
            bucket_name = self.bucket_name

        if not bucket_name:
            raise ValueError("Bucket name cannot be empty")

        try:
            response = self.client.createBucket(
                bucketName=bucket_name
            )
            if 200 <= response.status < 300:
                logger.info(f"Bucket {bucket_name} created successfully")
                return True
            else:
                logger.error(f"Bucket creation failed, status code: {response.status}")
                return False
        except Exception as e:
            logger.error(f"Bucket creation exception: {e}")
            return False

    def upload_file(self, local_path: str, obs_path: str, bucket_name: str = None,
                     enable_checkpoint: bool = True, part_size: int = 9437184) -> bool:
        """
        Upload a single file to OBS (supports large files natively with resumable upload)

        Args:
            local_path: Local file path
            obs_path: Target path on OBS
            bucket_name: Bucket name, use default bucket if not specified
            enable_checkpoint: Enable resumable upload, default is True
            part_size: Part size in bytes, default is 9MB (9437184)

        Returns:
            Return True if upload succeeds, otherwise False
        """
        if bucket_name is None:
            bucket_name = self.bucket_name

        if not bucket_name:
            raise ValueError("Bucket name cannot be empty")

        if not os.path.exists(local_path):
            logger.error(f"Local file does not exist: {local_path}")
            return False

        try:
            response = self.client.uploadFile(
                bucketName=bucket_name,
                objectKey=obs_path,
                uploadFile=local_path,
                enableCheckpoint=enable_checkpoint,
                partSize=part_size,
            )

            if response.status == 200:
                logger.info(f"File uploaded successfully: {local_path} -> {obs_path}")
                return True
            else:
                logger.error(f"File upload failed, status code: {response.status}")
                return False
        except Exception as e:
            logger.error(f"File upload exception: {e}")
            return False

    def download_file(self, obs_path: str, local_path: str, bucket_name: str = None,
                       enable_checkpoint: bool = True, part_size: int = 5242880) -> bool:
        """
        Download a single file from OBS (supports large files natively with resumable download)

        Args:
            obs_path: Source path on OBS
            local_path: Local target path
            bucket_name: Bucket name, use default bucket if not specified
            enable_checkpoint: Enable resumable download, default is True
            part_size: Part size in bytes, default is 5MB (5242880)

        Returns:
            Return True if download succeeds, otherwise False
        """
        if bucket_name is None:
            bucket_name = self.bucket_name

        if not bucket_name:
            raise ValueError("Bucket name cannot be empty")

        try:
            # If dir, create local dir and return
            if obs_path.endswith("/"):
                os.makedirs(local_path, exist_ok=True)
                logger.info(f"Dir create successfully: {obs_path} -> {local_path}")
                return True

            # Ensure local directory exists
            local_dir = os.path.dirname(local_path)
            if local_dir and not os.path.exists(local_dir):
                os.makedirs(local_dir)

            response = self.client.downloadFile(
                bucketName=bucket_name,
                objectKey=obs_path,
                downloadFile=local_path,
                enableCheckpoint=enable_checkpoint,
                partSize=part_size,
            )

            if response.status == 200:
                logger.info(f"File downloaded successfully: {obs_path} -> {local_path}")
                return True
            else:
                logger.error(f"File download failed, status code: {response.status}")
                return False
        except Exception as e:
            logger.error(f"File download exception: {e}")
            return False

    def upload_folder(self, local_folder: str, obs_prefix: str, bucket_name: str = None,
                       show_progress: bool = False, overwrite: bool = True,
                       enable_checkpoint: bool = True, part_size: int = 9437184) -> bool:
        """
        Upload an entire folder to OBS

        Args:
            local_folder: Local folder path
            obs_prefix: Prefix of folder on OBS
            bucket_name: Bucket name, use default bucket if not specified
            show_progress: Whether to show upload progress, default is False
            overwrite: Whether to overwrite existing objects, default is True
            enable_checkpoint: Enable resumable upload, default is True
            part_size: Part size in bytes, default is 9MB (9437184)

        Returns:
            Return True if all files upload successfully, otherwise False
        """
        if bucket_name is None:
            bucket_name = self.bucket_name

        if not bucket_name:
            raise ValueError("Bucket name cannot be empty")

        if not os.path.exists(local_folder):
            logger.error(f"Local folder does not exist: {local_folder}")
            return False

        success_count = 0
        total_count = 0

        try:
            file_list = []
            for root, dirs, files in os.walk(local_folder):
                for file in files:
                    local_path = os.path.join(root, file)
                    file_list.append(local_path)

            total_count = len(file_list)

            if total_count == 0:
                logger.info("No files to upload in folder")
                return True

            progress_bar = None
            task = None
            if show_progress:
                progress_bar = Progress(
                    TextColumn("[progress.description]{task.description}"),
                    BarColumn(),
                    TaskProgressColumn(),
                    TimeRemainingColumn(),
                )
                progress_bar.__enter__()
                task = progress_bar.add_task("Uploading files", total=total_count)

            for local_path in file_list:
                rel_path = os.path.relpath(local_path, local_folder)
                obs_path = os.path.join(obs_prefix, rel_path).replace("\\", "/")

                if not overwrite and self.head_object(obs_path, bucket_name):
                    success_count += 1
                    if progress_bar:
                        progress_bar.update(task, advance=1, description=f"Skipped (exists): {rel_path} ({success_count}/{total_count})")
                    continue

                if progress_bar:
                    progress_bar.update(task, description=f"Uploading: {rel_path}")

                if self.upload_file(local_path, obs_path, bucket_name, enable_checkpoint=enable_checkpoint, part_size=part_size):
                    success_count += 1
                    if progress_bar:
                        progress_bar.update(task, advance=1, description=f"Uploaded: {rel_path} ({success_count}/{total_count})")

            if progress_bar:
                progress_bar.__exit__(None, None, None)
                logger.info(f"Folder upload completed: {success_count}/{total_count} files uploaded successfully")
            return success_count == total_count
        except Exception as e:
            logger.error(f"Folder upload exception: {e}")
            return False

    def download_folder(self, obs_prefix: str, local_folder: str, bucket_name: str = None,
                         show_progress: bool = False, enable_checkpoint: bool = True,
                         part_size: int = 5242880) -> bool:
        """
        Download an entire folder from OBS

        Args:
            obs_prefix: Prefix of folder on OBS
            local_folder: Local target folder path
            bucket_name: Bucket name, use default bucket if not specified
            show_progress: Whether to show download progress, default is False
            enable_checkpoint: Enable resumable download, default is True
            part_size: Part size in bytes, default is 5MB (5242880)

        Returns:
            Return True if all files download successfully, otherwise False
        """
        if bucket_name is None:
            bucket_name = self.bucket_name

        if not bucket_name:
            raise ValueError("Bucket name cannot be empty")

        try:
            objects = self.list_objects(prefix=obs_prefix, max_keys=1000, bucket_name=bucket_name)

            if not objects:
                logger.info(f"No objects found with prefix '{obs_prefix}'")
                return True

            total_count = len(objects)
            success_count = 0

            progress_bar = None
            task = None
            if show_progress:
                progress_bar = Progress(
                    TextColumn("[progress.description]{task.description}"),
                    BarColumn(),
                    TaskProgressColumn(),
                    TimeRemainingColumn(),
                )
                progress_bar.__enter__()
                task = progress_bar.add_task("Downloading files", total=total_count)

            for obj in objects:
                obs_path = obj['key']
                rel_path = obs_path[len(obs_prefix):].lstrip('/')
                local_path = os.path.join(local_folder, rel_path)

                if progress_bar:
                    progress_bar.update(task, description=f"Downloading: {rel_path}")

                if self.download_file(obs_path, local_path, bucket_name, enable_checkpoint=enable_checkpoint, part_size=part_size):
                    success_count += 1
                    if progress_bar:
                        progress_bar.update(task, advance=1, description=f"Downloaded: {rel_path} ({success_count}/{total_count})")

            if progress_bar:
                progress_bar.__exit__(None, None, None)
                logger.info(f"Folder download completed: {success_count}/{total_count} files downloaded successfully")
            return success_count == total_count
        except Exception as e:
            logger.error(f"Folder download exception: {e}")
            return False

    def delete_object(self, obs_path: str, bucket_name: str = None) -> bool:
        """
        Delete object from OBS
        
        Args:
            obs_path: Object path on OBS
            bucket_name: Bucket name, use default bucket if not specified
            
        Returns:
            Return True if deletion succeeds, otherwise False
        """
        if bucket_name is None:
            bucket_name = self.bucket_name
            
        if not bucket_name:
            raise ValueError("Bucket name cannot be empty")
            
        try:
            response = self.client.deleteObject(
                bucketName=bucket_name,
                objectKey=obs_path
            )
            
            if 200 <= response.status < 300:
                logger.info(f"Object deleted successfully: {obs_path}")
                return True
            else:
                logger.error(f"Object deletion failed, status code: {response.status}")
                return False
        except Exception as e:
            logger.error(f"Object deletion exception: {e}")
            return False

    def generate_presigned_url(self, obs_path: str, expires: int = 3600,
                                bucket_name: str = None) -> str:
        """
        Generate presigned URL (GET method only)

        Args:
            obs_path: Object path on OBS
            expires: Expiration time (seconds), default is 3600 seconds (1 hour)
            bucket_name: Bucket name, use default bucket if not specified

        Returns:
            Presigned URL string
        """
        if bucket_name is None:
            bucket_name = self.bucket_name

        if not bucket_name:
            raise ValueError("Bucket name cannot be empty")

        try:
            response = self.client.createSignedUrl(
                method='GET',
                bucketName=bucket_name,
                objectKey=obs_path,
                expires=expires,
            )

            if response and response.signedUrl:
                return response.signedUrl
            else:
                logger.error("Presigned URL generation failed, response is None")
                return ""
        except Exception as e:
            logger.error(f"Presigned URL generation exception: {e}")
            return ""

    def list_objects(self, prefix: str = "", max_keys: int = 1000, bucket_name: str = None) -> list:
        """
        List objects in bucket
        
        Args:
            prefix: Object name prefix filter
            max_keys: Maximum number of objects to return (default 1000)
            bucket_name: Bucket name, use default bucket if not specified
            
        Returns:
            Object list
        """
        if bucket_name is None:
            bucket_name = self.bucket_name
            
        if not bucket_name:
            raise ValueError("Bucket name cannot be empty")
            
        try:
            objects = []
            marker = None
            
            while True:
                list_kwargs = {
                    'bucketName': bucket_name,
                    'prefix': prefix,
                    'max_keys': max_keys
                }
                
                if marker:
                    list_kwargs['marker'] = marker
                    
                response = self.client.listObjects(**list_kwargs)
                
                if response.status != 200:
                    logger.error(f"Listing objects failed, status code: {response.status}")
                    return []
                
                if response.body.contents:
                    for obj in response.body.contents:
                        objects.append({
                            'key': obj.key,
                            'size': obj.size,
                            'last_modified': obj.lastModified,
                            'etag': obj.etag
                        })
                
                if getattr(response, 'isTruncated', False) and getattr(response, 'nextMarker', None):
                    marker = response.nextMarker
                else:
                    break
                    
            return objects
        except Exception as e:
            logger.error(f"Listing objects exception: {e}")
            return []

    def head_object(self, obs_path: str, bucket_name: str = None) -> Optional[Dict[str, Any]]:
        """
        Get object metadata

        Args:
            obs_path: Object path on OBS
            bucket_name: Bucket name, use default bucket if not specified

        Returns:
            Object metadata dictionary if exists, None if not found
        """
        if bucket_name is None:
            bucket_name = self.bucket_name

        if not bucket_name:
            raise ValueError("Bucket name cannot be empty")

        try:
            response = self.client.getObjectMetadata(
                bucketName=bucket_name,
                objectKey=obs_path
            )

            if response.status == 200:
                return {
                    'content_length': response.body.contentLength,
                    'content_type': response.body.contentType,
                    'etag': response.body.etag,
                    'last_modified': response.body.lastModified,
                }
            elif response.status == 404:
                return None
            else:
                logger.debug("Failed to get object metadata, status code: %s", response.status)
                return None
        except Exception as e:
            logger.error(f"Exception getting object metadata: {e}")
            return None
