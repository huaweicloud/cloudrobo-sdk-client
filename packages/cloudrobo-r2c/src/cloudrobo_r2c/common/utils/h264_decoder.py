import dataclasses
import logging
from typing import List, Optional

import av
import numpy as np

try:
    import cv2
except ImportError:
    cv2 = None

logger = logging.getLogger(__name__)


class H264Decoder:
    """H264 decoder"""

    def __init__(self):
        """Initialize H264 decoder"""
        self.codec = av.CodecContext.create('h264', 'r')

    def decode_and_split(self, processed_data):
        """
        Decode H.264 data and split it back into original images

        Args:
            processed_data: Dictionary containing encoded image data

        Returns:
            tuple: (restored processed_data, compression info dictionary)
        """
        try:
            result_data = processed_data.copy()

            # Validate input data format
            if not isinstance(processed_data, dict) or "images" not in processed_data:
                raise Exception("Invalid input data format: missing 'images' field")

            images_data = processed_data["images"]

            if not isinstance(images_data, dict) or "h264_data" not in images_data:
                logger.warning("Input data is not encoded, returning original data directly")
                compression_info = {
                    'encoded_size': 0,
                    'decoded_size': 0,
                    'compression_ratio': 0
                }
                return processed_data, compression_info

            encoded_data = images_data["h264_data"]
            metadata = images_data["metadata"]

            # Validate metadata completeness
            required_fields = ['image_keys', 'original_shapes', 'combined_shape', 'frame_count']
            for field in required_fields:
                if field not in metadata:
                    raise Exception(f"Metadata missing required field: {field}")

            image_keys = metadata['image_keys']
            original_shapes = metadata['original_shapes']
            combined_shape = metadata['combined_shape']
            merge_direction = metadata.get('merge_direction', 'horizontal')
            frame_count = metadata['frame_count']

            # Calculate pre-encoding data size (total size of raw image data)
            original_size = self._calculate_original_size(original_shapes)
            encoded_size = len(encoded_data)

            # Decode H.264 data
            combined_image = self._decode_h264(encoded_data)

            # Calculate decoded data size
            decoded_size = self._calculate_image_size(combined_image)

            # Split merged image
            split_images = self._split_combined_image(
                combined_image, frame_count, original_shapes, merge_direction
            )

            # Rebuild image dictionary (restore by image_keys)
            restored_images = self._rebuild_image_dict(image_keys, split_images, original_shapes)

            # Update images in result_data to restored image dictionary
            result_data["images"] = restored_images

            # Calculate compression ratio
            compression_ratio = encoded_size / original_size if original_size > 0 else 0

            compression_info = {
                'original_size': original_size,      # Raw data size before encoding
                'encoded_size': encoded_size,        # Data size after H.264 encoding
                'decoded_size': decoded_size,        # Image data size after decoding
                'compression_ratio': compression_ratio,  # Compression ratio
                'frame_count': frame_count,          # Number of images
                'original_shapes': original_shapes   # Original image shapes
            }

            return result_data, compression_info

        except Exception:
            logger.exception("Error during decoding")
            raise

    def _calculate_original_size(self, original_shapes):
        """Calculate total size of original image data"""
        total_size = 0
        for shape in original_shapes:
            # Size of one image: height * width * channels * bytes_per_pixel (uint8 = 1 byte)
            image_size = shape[0] * shape[1] * shape[2] * 1  # uint8 type, 1 byte/pixel
            total_size += image_size
        return total_size

    def _calculate_image_size(self, image):
        """Calculate image data size"""
        if isinstance(image, np.ndarray):
            # Image size = number of pixels * bytes per pixel
            return image.size * image.itemsize
        return 0

    def _decode_h264(self, encoded_data):
        """Decode H.264 data"""
        try:
            # Create packet and decode
            packet = av.Packet(encoded_data)
            frames = list(self.codec.decode(packet))

            if not frames:
                raise Exception("H.264 decoding failed: no frame was generated")

            # Get the first frame (assuming there is only one frame)
            decoded_frame = frames[0]
            combined_image = decoded_frame.to_ndarray(format='bgr24')

            return combined_image

        except Exception as e:
            raise Exception(f"H.264 decoding failed: {e}")

    def _split_combined_image(self, combined_image, frame_count, original_shapes, merge_direction):
        """Split merged image"""
        try:
            if frame_count == 1:
                return [combined_image]

            if merge_direction == 'horizontal':
                return self._split_horizontal(combined_image, original_shapes)
            elif merge_direction == 'vertical':
                return self._split_vertical(combined_image, original_shapes)
            else:
                raise Exception(f"Unsupported merge direction: {merge_direction}")

        except Exception as e:
            raise Exception(f"Image split failed: {e}")

    def _split_horizontal(self, combined_image, original_shapes):
        """Split image horizontally"""
        images = []
        current_x = 0

        for i, original_shape in enumerate(original_shapes):
            original_height, original_width, channels = original_shape

            # Calculate end position of current image
            next_x = current_x + original_width

            # Ensure boundary is not exceeded
            if next_x > combined_image.shape[1]:
                logger.warning("Image %s width exceeds boundary, adjusting", i)
                next_x = combined_image.shape[1]

            # Extract sub-image
            sub_image = combined_image[:, current_x:next_x, :]

            # Adjust if height does not match
            if sub_image.shape[0] != original_height:
                sub_image = self._resize_image(sub_image, (original_height, sub_image.shape[1]))

            images.append(sub_image)
            current_x = next_x

            # Stop early if boundary is reached
            if current_x >= combined_image.shape[1]:
                break

        return images

    def _split_vertical(self, combined_image, original_shapes):
        """Split image vertically"""
        images = []
        current_y = 0

        for i, original_shape in enumerate(original_shapes):
            original_height, original_width, channels = original_shape

            # Calculate end position of current image
            next_y = current_y + original_height

            # Ensure boundary is not exceeded
            if next_y > combined_image.shape[0]:
                logger.warning("Image %s height exceeds boundary, adjusting", i)
                next_y = combined_image.shape[0]

            # Extract sub-image
            sub_image = combined_image[current_y:next_y, :, :]

            # Adjust if width does not match
            if sub_image.shape[1] != original_width:
                sub_image = self._resize_image(sub_image, (sub_image.shape[0], original_width))

            images.append(sub_image)
            current_y = next_y

            # Stop early if boundary is reached
            if current_y >= combined_image.shape[0]:
                break

        return images

    def _resize_image(self, image, target_size):
        """Resize image"""
        target_height, target_width = target_size

        if image.shape[0] == target_height and image.shape[1] == target_width:
            return image

        if cv2 is not None:
            return cv2.resize(image, (target_width, target_height), interpolation=cv2.INTER_LINEAR)

        # Simple numpy implementation (nearest-neighbor interpolation)
        logger.warning("OpenCV not found, using a simple resize method")
        height_ratio = target_height / image.shape[0]
        width_ratio = target_width / image.shape[1]

        resized = np.zeros((target_height, target_width, image.shape[2]), dtype=image.dtype)

        for i in range(target_height):
            for j in range(target_width):
                src_i = min(int(i / height_ratio), image.shape[0] - 1)
                src_j = min(int(j / width_ratio), image.shape[1] - 1)
                resized[i, j] = image[src_i, src_j]

        return resized

    def _rebuild_image_dict(self, image_keys, split_images, original_shapes):
        """Rebuild image dictionary (restore to key: image_array format by image_keys)"""
        if len(image_keys) != len(split_images):
            raise Exception(
                f"Image key count ({len(image_keys)}) does not match split image count ({len(split_images)})"
            )

        restored_images = {}

        for i, (key, image, original_shape) in enumerate(zip(image_keys, split_images, original_shapes)):
            # Final shape validation
            if image.shape != original_shape:
                image = self._resize_image(image, (original_shape[0], original_shape[1]))

            restored_images[key] = image

        return restored_images

    def close(self):
        """Release resources"""
        if self.codec is None:
            return 
        try:
            self.codec.close()
        finally:
            self.codec = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


@dataclasses.dataclass
class H264Statistics:
    h264_config: Optional[dict] = None
    episode_count: int = 0
    frames: Optional[List[dict]] = None
    success_rate: Optional[float] = None
    output_file: Optional[str] = None
