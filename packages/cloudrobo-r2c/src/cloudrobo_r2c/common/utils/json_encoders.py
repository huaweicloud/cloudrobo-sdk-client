"""Helper utilities related to JSON serialization."""

import base64
import json


class BytesEncoder(json.JSONEncoder):
    """Custom JSON encoder that automatically converts bytes to Base64 strings."""

    def default(self, obj):
        if isinstance(obj, bytes):
            # Convert to ASCII string for JSON serialization
            return base64.b64encode(obj).decode('ascii')
        return super().default(obj)
