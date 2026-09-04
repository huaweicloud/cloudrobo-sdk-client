import dataclasses
import logging
from typing import Dict
from typing import List

import av
import numpy as np

logger = logging.getLogger(__name__)

# -----------------------------
# Default codec/runtime constants
# -----------------------------
DEFAULT_INPUT_FRAME_FORMAT = "bgr24"
DEFAULT_CODEC_PIX_FMT = "yuv420p"
DEFAULT_CODEC_FRAMERATE = 20
DEFAULT_X264_TUNE = "zerolatency"
DEFAULT_X264_PARAMS = "keyint=1:min-keyint=1:repeat-headers=1"


class H264Preset:
    VERY_LOW = "very_low"
    LOW = "low"
    BALANCED = "balanced"
    HIGH = "high"
    VERY_HIGH = "very_high"
    VERY_HIGH_SPEED_ULTRAFAST = "very_high_speed_ultrafast"
    VERY_HIGH_SPEED_MEDIUM = "very_high_speed_medium"


PRESET_CONFIGS = {
    H264Preset.VERY_LOW: {
        "bitrate": 100000,  # 100 kbps
        "crf": 32,
        "speed_preset": "ultrafast",  # Ultra-fast encoding, low compression efficiency
        "description": "Very low quality with obvious artifacts, suitable for extremely low bandwidth"
    },
    H264Preset.LOW: {
        "bitrate": 300000,  # 300 kbps
        "crf": 28,
        "speed_preset": "veryfast",  # Very fast encoding
        "description": "Low quality with mild artifacts, suitable for poor networks"
    },
    H264Preset.BALANCED: {
        "bitrate": 800000,  # 800 kbps
        "crf": 23,
        "speed_preset": "medium",  # Balanced encoding
        "description": "Balanced quality with no obvious artifacts, suitable for general scenarios"
    },
    H264Preset.HIGH: {
        "bitrate": 2000000,  # 2 Mbps
        "crf": 20,
        "speed_preset": "slow",  # Slower encoding, higher compression efficiency
        "description": "High quality with clear details, suitable for high-quality requirements"
    },
    H264Preset.VERY_HIGH: {
        "bitrate": 5000000,  # 5 Mbps
        "crf": 16,
        "speed_preset": "slower",  # Even slower encoding, highest compression efficiency
        "description": "Very high quality with best details, suitable for high-quality recording"
    },
    H264Preset.VERY_HIGH_SPEED_ULTRAFAST: {
        "bitrate": 5000000,  # 5 Mbps
        "crf": 16,
        "speed_preset": "ultrafast",  # Fastest encoding
        "description": "Very high quality with ultra-fast encoding speed, suitable for high-quality recording"
    },
    H264Preset.VERY_HIGH_SPEED_MEDIUM: {
        "bitrate": 5000000,  # 5 Mbps
        "crf": 16,
        "speed_preset": "medium",  # Fast encoding
        "description": "Very high quality with very fast encoding speed, suitable for high-quality recording"
    }
}


class H264ImageEncoder:
    """H.264 image encoder supporting multiple quality presets"""

    def __init__(
        self,
        preset=H264Preset.VERY_HIGH,
        codec_name='h264',
        rate: float = 10,
        input_frame_format: str = DEFAULT_INPUT_FRAME_FORMAT,
        codec_pix_fmt: str = DEFAULT_CODEC_PIX_FMT,
        codec_framerate: int = DEFAULT_CODEC_FRAMERATE,
        x264_tune: str = DEFAULT_X264_TUNE,
        x264_params: str = DEFAULT_X264_PARAMS,
    ):
        """
        Initialize the encoder

        Args:
            preset: Preset quality level, using H264Preset class attributes
            codec_name: Encoder name
            rate: Reserved legacy parameter (kept for backward compatibility)
            input_frame_format: Input ndarray format for av.VideoFrame.from_ndarray
            codec_pix_fmt: Output codec pixel format
            codec_framerate: Codec framerate
            x264_tune: x264 tune option (used when codec_name == 'h264')
            x264_params: x264-params option (used when codec_name == 'h264')
        """
        self.codec = None
        self.rate = rate
        self.codec_name = codec_name
        self.preset = preset
        self.current_config = PRESET_CONFIGS.get(preset, PRESET_CONFIGS[H264Preset.BALANCED])

        self.input_frame_format = input_frame_format
        self.codec_pix_fmt = codec_pix_fmt
        self.codec_framerate = codec_framerate
        self.x264_tune = x264_tune
        self.x264_params = x264_params

        self._validate_codec_runtime_config()
        self._initialize_codec()

    def _validate_codec_runtime_config(self):
        """Validate configurable codec runtime options."""
        if not isinstance(self.input_frame_format, str) or not self.input_frame_format:
            raise ValueError("input_frame_format must be a non-empty string")
        if not isinstance(self.codec_pix_fmt, str) or not self.codec_pix_fmt:
            raise ValueError("codec_pix_fmt must be a non-empty string")
        if not isinstance(self.codec_framerate, int) or self.codec_framerate <= 0:
            raise ValueError("codec_framerate must be a positive integer")
        if not isinstance(self.x264_tune, str) or not self.x264_tune:
            raise ValueError("x264_tune must be a non-empty string")
        if not isinstance(self.x264_params, str) or not self.x264_params:
            raise ValueError("x264_params must be a non-empty string")

    def _initialize_codec(self):
        """Initialize the H.264 encoder"""
        try:
            # Create encoder
            self.codec = av.CodecContext.create(self.codec_name, 'w')
            # Set encoding parameters
            if self.codec_name == 'h264':
                self.codec.options = {
                    'preset': self.current_config['speed_preset'],  # Use encoding speed preset from config
                    'crf': str(self.current_config['crf']),
                    'tune': self.x264_tune,
                    'x264-params': self.x264_params
                }

            logger.info("Encoder initialized - quality preset: %s", self.preset)
            logger.info("  - Encoding speed: %s", self.current_config['speed_preset'])
            logger.info("  - CRF: %s", self.current_config['crf'])
            logger.info("  - Target bitrate: %s bps", self.current_config['bitrate'])
            logger.info("  - Description: %s", self.current_config['description'])
            logger.info("  - Input frame format: %s", self.input_frame_format)
            logger.info("  - Codec pixel format: %s", self.codec_pix_fmt)
            logger.info("  - Codec framerate: %s", self.codec_framerate)
            logger.info("  - x264 tune: %s", self.x264_tune)
            logger.info("  - x264 params: %s", self.x264_params)

        except Exception as e:
            raise Exception(f"Failed to initialize H.264 encoder: {e}")

    def _resolve_temp_config(self, preset):
        """Resolve and return the config used for this encoding run."""
        if preset and preset in PRESET_CONFIGS:
            temp_config = PRESET_CONFIGS[preset]
            logger.info("Using temporary quality preset: %s", preset)
            logger.info("  - Encoding speed: %s", temp_config['speed_preset'])
            logger.info("  - CRF: %s", temp_config['crf'])
            logger.info("  - Description: %s", temp_config['description'])
            return temp_config
        return self.current_config

    @staticmethod
    def _validate_and_get_images_dict(processed_data):
        """Validate input and return the images dictionary."""
        if not isinstance(processed_data, dict) or "images" not in processed_data:
            raise Exception("Invalid input data format: missing 'images' field")

        images_dict = processed_data["images"]
        if not isinstance(images_dict, dict):
            raise Exception("'images' field must be a dictionary")

        return images_dict

    @staticmethod
    def _collect_valid_images(images_dict):
        """
        Collect and validate image data.

        Returns:
            tuple: (images, image_keys, original_shapes)
        """
        images = []
        image_keys = []
        original_shapes = []
        original_sizes = []

        for key, image in images_dict.items():
            if not isinstance(image, np.ndarray):
                raise Exception(f"Image {key} is not a numpy array")

            # Validate image format
            if len(image.shape) == 3 and image.shape[2] == 3:
                images.append(image)
                image_keys.append(key)
                original_shapes.append(image.shape)
                # Calculate single image size (keep original logic)
                image_size = image.shape[0] * image.shape[1] * image.shape[2] * image.itemsize
                original_sizes.append(image_size)
            else:
                raise Exception(f"Invalid image format for {key}: {image.shape}")

        return images, image_keys, original_shapes

    @staticmethod
    def _combine_images(images):
        """Combine images using the original logic (single image unchanged, multiple images horizontally concatenated)."""
        if len(images) == 1:
            return images[0]
        return np.hstack(images)

    def _prepare_codec_for_encoding(self, combined_image, temp_config):
        """
        Set encoder parameters and apply temporary preset/crf.

        Returns:
            tuple: (original_speed_preset, original_crf)
        """
        # Set encoder parameters
        self.codec.width = combined_image.shape[1]
        self.codec.height = combined_image.shape[0]
        self.codec.pix_fmt = self.codec_pix_fmt
        self.codec.framerate = self.codec_framerate
        self.codec.bit_rate = temp_config['bitrate']

        # Record original options
        original_speed_preset = self.codec.options.get('preset')
        original_crf = self.codec.options.get('crf')

        # Apply temporary options
        self.codec.options['preset'] = temp_config['speed_preset']
        if temp_config['crf'] is None:
            # Pure bitrate mode: remove CRF
            if 'crf' in self.codec.options:
                del self.codec.options['crf']
        else:
            self.codec.options['crf'] = str(temp_config['crf'])

        return original_speed_preset, original_crf

    def _restore_codec_options(self, original_speed_preset, original_crf):
        """Restore encoder preset/crf options (keep original behavior: restore only when original values are truthy)."""
        if original_speed_preset:
            self.codec.options['preset'] = original_speed_preset
        if original_crf:
            self.codec.options['crf'] = original_crf

    @staticmethod
    def _packets_to_bytes(packets):
        """Concatenate encoded packet list into bytes."""
        encoded_data = b''
        for packet in packets:
            if hasattr(packet, 'to_bytes'):
                encoded_data += packet.to_bytes()
            else:
                encoded_data += bytes(packet)
        return encoded_data

    @staticmethod
    def _build_compression_info(
        original_total_size,
        encoded_size,
        target_bitrate,
        image_count,
        original_shapes,
        combined_shape,
        image_keys,
        quality_preset_used,
        speed_preset_used,
        crf_used
    ):
        """Build compression info dictionary (fields kept consistent with the original implementation)."""
        compression_ratio = encoded_size / original_total_size if original_total_size > 0 else 0
        actual_bitrate = (encoded_size * 8)  # Convert to bits

        return {
            'original_total_size': original_total_size,
            'encoded_size': encoded_size,
            'compression_ratio': compression_ratio,
            'compression_factor': original_total_size / encoded_size if encoded_size > 0 else 0,  # Compression factor
            'target_bitrate': target_bitrate,
            'actual_bitrate': actual_bitrate,
            'bitrate_achievement': actual_bitrate / target_bitrate if target_bitrate > 0 else 0,
            'frame_count': image_count,
            'original_shapes': original_shapes,
            'combined_shape': combined_shape,
            'image_keys': image_keys,
            'quality_preset_used': quality_preset_used,
            'speed_preset_used': speed_preset_used,
            'crf_used': crf_used
        }

    @staticmethod
    def _build_encoded_images_payload(
        encoded_data,
        image_keys,
        original_shapes,
        combined_shape,
        frame_count,
        quality_preset,
        speed_preset,
        crf_used
    ):
        """Build the H264 output structure for result_data['images'].""" 
        return {
            "h264_data": encoded_data,
            "metadata": {
                'image_keys': image_keys,
                'original_shapes': original_shapes,
                'combined_shape': combined_shape,
                'merge_direction': 'horizontal',
                'frame_count': frame_count,
                'quality_preset': quality_preset,
                'speed_preset': speed_preset,
                'crf_used': crf_used
            }
        }

    def encode_images(self, processed_data, preset=None):
        """
        Encode image data

        Args:
            processed_data: Dictionary containing raw image data
            preset: Optional temporary override for current preset

        Returns:
            tuple: (encoded processed_data, compression info dictionary)
        """
        temp_config = self._resolve_temp_config(preset)

        try:
            # Create a copy of result data
            result_data = processed_data.copy()

            # Validate input and get image dictionary
            images_dict = self._validate_and_get_images_dict(processed_data)

            # Collect and validate images
            images, image_keys, original_shapes = self._collect_valid_images(images_dict)
            if not images:
                raise Exception("No valid images found")

            # Combine images (horizontal concatenation)
            combined_image = self._combine_images(images)

            # Calculate combined image size
            original_total_size = self._calculate_image_size(combined_image)

            # Build AV frame
            av_frame = av.VideoFrame.from_ndarray(combined_image, format=self.input_frame_format)

            # Set temporary encoding parameters and encode; ensure options are restored afterward
            original_speed_preset = None
            original_crf = None
            try:
                original_speed_preset, original_crf = self._prepare_codec_for_encoding(combined_image, temp_config)
                packets = self.codec.encode(av_frame)
            finally:
                self._restore_codec_options(original_speed_preset, original_crf)

            # Concatenate all packet data
            encoded_data = self._packets_to_bytes(packets)
            encoded_size = len(encoded_data)

            # Build compression info
            quality_preset_used = preset if preset else self.preset
            compression_info = self._build_compression_info(
                original_total_size=original_total_size,
                encoded_size=encoded_size,
                target_bitrate=temp_config['bitrate'],
                image_count=len(images),
                original_shapes=original_shapes,
                combined_shape=combined_image.shape,
                image_keys=image_keys,
                quality_preset_used=quality_preset_used,
                speed_preset_used=temp_config['speed_preset'],
                crf_used=temp_config['crf']
            )

            # Replace images with encoded data
            result_data["images"] = self._build_encoded_images_payload(
                encoded_data=encoded_data,
                image_keys=image_keys,
                original_shapes=original_shapes,
                combined_shape=combined_image.shape,
                frame_count=len(images),
                quality_preset=quality_preset_used,
                speed_preset=temp_config['speed_preset'],
                crf_used=temp_config['crf']
            )

            return result_data, compression_info

        except Exception:
            logger.exception("Error during encoding")
            raise

    def get_available_presets(self):
        """Get all available preset configurations"""
        return PRESET_CONFIGS

    def set_preset(self, preset):
        """Dynamically switch preset"""
        if preset not in PRESET_CONFIGS:
            available = list(PRESET_CONFIGS.keys())
            raise ValueError(f"Unsupported preset: {preset}. Available presets: {available}")

        self.preset = preset
        self.current_config = PRESET_CONFIGS[preset]

        # Reinitialize encoder to apply new settings
        self.close()
        self._initialize_codec()
        logger.info("Switched to quality preset: %s - %s", preset, self.current_config['description'])

    def get_current_preset_info(self):
        """Get current preset information"""
        return {
            'quality_preset': self.preset,
            'speed_preset': self.current_config['speed_preset'],
            'config': self.current_config,
            'description': self.current_config['description']
        }

    def _calculate_image_size(self, image):
        """Calculate image data size"""
        if isinstance(image, np.ndarray):
            return image.size * image.itemsize
        return 0

    def close(self):
        """Release encoder resources"""
        if not self.codec:
            return

        flush_error = None
        try:
            # Flush encoder to retrieve remaining packets
            packets = self.codec.encode(None)
            for _ in packets:
                continue
        except Exception as e:
            flush_error = e
        finally:
            self.codec = None

        if flush_error is not None:
            raise RuntimeError(f"Failed to flush remaining encoder packets: {flush_error}") from flush_error
