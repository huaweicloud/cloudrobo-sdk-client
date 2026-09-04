import re
from typing import Dict, Set

TYPE_SUBTYPE_MAP: Dict[str, Set[str]] = {
    "model": set(),
    "dataset": set(),
    "algorithm": {"inference", "data_processing", "training", "data_evaluating", "rl"},
    "image": {"inference", "data_processing", "training", "notebook", "rl"},
    "simulation": {"robot", "environment", "object", "scene"},
}

VALID_TYPES = set(TYPE_SUBTYPE_MAP.keys())

VALID_STATUSES = {"CREATING", "DRAFT", "ALPHA", "BETA", "RELEASE", "STABLE", "DEPRECATED", "ARCHIVE"}

VALID_MODEL_TYPES = {"planning", "perception", "vla", "vln"}

VALID_ROBOT_TYPES = {"humanoid", "mobile_manipulator", "robot_arm", "quadruped_robot", "wheeled_robot", "other"}

VALID_ARCH_TYPES = {"x86_64", "arm"}

VALID_DEVICE_TYPES = {"CPU", "GPU", "ASCEND"}

VALID_FLAVOR_TYPES = {"CPU", "GPU", "NPU"}

VALID_RESOURCE_VALUES = VALID_FLAVOR_TYPES | {"multiple", "singular"}

VALID_IMAGE_SOURCES = {"preset", "custom"}

SKILL_MODEL_TYPES = {"vla", "vln"}

UUID_PATTERN = re.compile(r"^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
NAME_PATTERN = re.compile(r"^[\u4e00-\u9fa5a-zA-Z0-9\-_./]{3,64}$")
TAG_PATTERN = re.compile(r"^[\u4e00-\u9fa5a-zA-Z0-9.-_ ]{1,32}$")
OBS_URL_PATTERN = re.compile(r"^obs://[a-z0-9][a-z0-9.\-]{1,61}[a-z0-9]/.{1,768}$")
SWR_URL_PATTERN = re.compile(r"^swr\.[a-zA-Z0-9._\-]+/[a-z0-9][a-z0-9._\-]*/[a-zA-Z0-9._\-]+:[a-zA-Z0-9._\-]+$")
GENERATION_METHOD_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,63}$")
VERSION_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._\-]{1,127}$")

ASSET_FIELD_RULES = {
    "catalog_id": {
        "required_on": "create",
        "pattern": UUID_PATTERN,
        "pattern_desc": "UUID format",
        "forbidden_on": "update",
    },
    "name": {
        "pattern": NAME_PATTERN,
        "pattern_desc": "3-64 chars: Chinese, English, digits, hyphen, underscore, dot, slash",
    },
    "description": {
        "max_length": 512,
    },
    "status": {
        "enum": VALID_STATUSES,
    },
    "tags": {
        "max_items": 100,
        "item_pattern": TAG_PATTERN,
        "item_pattern_desc": "1-32 chars: Chinese, English, digits, dot, hyphen, underscore, space",
    },
    "url": {
        "max_length": 1024,
        "patterns": [OBS_URL_PATTERN, SWR_URL_PATTERN],
        "pattern_desc": "obs://bucket/path or SWR image format",
        "forbidden_on": "update",
    },
    "generation_method": {
        "pattern": GENERATION_METHOD_PATTERN,
        "pattern_desc": "1-64 chars: starts with letter, then letters/digits/underscore",
        "forbidden_on": "update",
    },
    "parent_asset_version_id": {
        "pattern": UUID_PATTERN,
        "pattern_desc": "UUID format",
        "forbidden_on": "update",
    },
}

VERSION_FIELD_RULES = {
    "version": {
        "pattern": VERSION_PATTERN,
        "pattern_desc": "2-128 chars: starts with alphanumeric, then alphanumeric/dot/hyphen/underscore",
    },
    "description": {
        "max_length": 512,
    },
    "status": {
        "enum": VALID_STATUSES,
    },
    "url": {
        "max_length": 1024,
        "patterns": [OBS_URL_PATTERN, SWR_URL_PATTERN],
        "pattern_desc": "obs://bucket/path or SWR image format",
        "forbidden_on": "update",
    },
    "parent_asset_version_id": {
        "pattern": UUID_PATTERN,
        "pattern_desc": "UUID format",
        "forbidden_on": "update",
    },
    "generation_method": {
        "pattern": GENERATION_METHOD_PATTERN,
        "pattern_desc": "1-64 chars: starts with letter, then letters/digits/underscore",
        "forbidden_on": "update",
    },
}

INPUT_OUTPUT_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
INPUT_OUTPUT_DESC_PATTERN = re.compile(r"^[\s\S]{0,512}$")
HYPER_PARAMS_NAME_PATTERN = re.compile(r"^[a-zA-Z_][A-Za-z0-9_.\-]{0,63}$")
HYPER_PARAMS_VALUE_PATTERN = re.compile(r"^[\u4e00-\u9fa5A-Za-z0-9_/\\.,:@<>{}$\-]{1,512}$")
HYPER_PARAMS_DESC_PATTERN = re.compile(r"^[^\\@#$%^&*<>]{0,256}$")
ENV_NAME_PATTERN = re.compile(r"^[a-zA-Z_][A-Za-z0-9_-]{0,63}$")
ENV_VALUE_PATTERN = re.compile(r"^[\u4e00-\u9fa5A-Za-z0-9_/\\.,:@<>{}$\-]{1,512}$")
SKILL_NAME_PATTERN = re.compile(r"(?!^\s)[\u4e00-\u9fa5a-zA-Z0-9\-_\s]{1,64}(?<!\s)$")
PROMPT_PATTERN = re.compile(r"^[\s\S]{1,1024}$")
ROBOT_MANUFACTURER_PATTERN = re.compile(r"^[\u4e00-\u9fa5a-zA-Z0-9][\u4e00-\u9fa5a-zA-Z0-9\-._ ]{0,63}$")

EXT_METADATA_RULES: Dict[str, Dict] = {
    "model": {
        "required_fields": ["model_type"],
        "fields": {
            "model_type": {"type": "string", "enum": VALID_MODEL_TYPES},
            "skills": {
                "type": "array",
                "max_items": 50,
                "item_fields": {
                    "name": {"type": "string", "pattern": SKILL_NAME_PATTERN, "required": True,
                             "pattern_desc": "1-64 chars: Chinese, English, digits, hyphen, underscore, space (no leading/trailing spaces)"},
                    "prompt": {"type": "string", "pattern": PROMPT_PATTERN, "required": True,
                               "pattern_desc": "1-1024 chars"},
                },
                "no_duplicate": "prompt",
            },
            "strict": {"type": "boolean"},
        },
    },
    "dataset": {
        "required_fields": ["annotation_status"],
        "fields": {
            "annotation_status": {"type": "boolean"},
        },
    },
    "algorithm": {
        "required_fields": ["engine", "command"],
        "fields": {
            "engine": {
                "type": "object",
                "required_fields": ["image_url"],
                "fields": {
                    "image_url": {"type": "string", "pattern": SWR_URL_PATTERN,
                                  "pattern_desc": "SWR image format: swr.{endpoint}/{namespace}/{repo}:{tag}"},
                    "image_source": {"type": "string", "enum": VALID_IMAGE_SOURCES, "required": True},
                },
            },
            "command": {"type": "string", "max_length": 4096},
            "code_dir": {"type": "string"},
            "boot_file": {"type": "string"},
            "inputs": {
                "type": "array",
                "max_items": 10,
                "item_fields": {
                    "name": {"type": "string", "pattern": INPUT_OUTPUT_NAME_PATTERN, "required": True,
                             "pattern_desc": "1-64 chars: English, digits, hyphen, underscore"},
                    "access_method": {"type": "string", "enum": {"env", "parameter"}, "required": True},
                    "description": {"type": "string", "pattern": INPUT_OUTPUT_DESC_PATTERN,
                                    "pattern_desc": "0-512 chars"},
                },
                "no_duplicate": "name",
            },
            "outputs": {
                "type": "array",
                "max_items": 5,
                "item_fields": {
                    "name": {"type": "string", "pattern": INPUT_OUTPUT_NAME_PATTERN, "required": True,
                             "pattern_desc": "1-64 chars: English, digits, hyphen, underscore"},
                    "access_method": {"type": "string", "enum": {"env", "parameter"}, "required": True},
                    "description": {"type": "string", "pattern": INPUT_OUTPUT_DESC_PATTERN,
                                    "pattern_desc": "0-512 chars"},
                },
                "no_duplicate": "name",
            },
            "hyperparams": {
                "type": "array",
                "max_items": 90,
                "item_fields": {
                    "name": {"type": "string", "pattern": HYPER_PARAMS_NAME_PATTERN, "required": True,
                             "pattern_desc": "1-64 chars: starts with letter/underscore, may contain dot/hyphen"},
                    "default": {"type": "string", "required": True},
                    "constraint": {
                        "type": "object",
                        "required": True,
                        "required_fields": ["type", "editable", "required", "sensitive"],
                    },
                    "description": {"type": "string", "pattern": HYPER_PARAMS_DESC_PATTERN,
                                    "pattern_desc": "0-256 chars, no \\ @ # $ % ^ & * < >"},
                },
                "no_duplicate": "name",
            },
            "environment_variables": {
                "type": "array",
                "max_items": 90,
                "item_fields": {
                    "name": {"type": "string", "pattern": ENV_NAME_PATTERN, "required": True,
                             "pattern_desc": "1-64 chars: starts with letter/underscore, may contain hyphen"},
                    "default": {"type": "string", "pattern": ENV_VALUE_PATTERN, "required": True,
                                "pattern_desc": "1-512 chars"},
                    "description": {"type": "string", "max_length": 512},
                },
                "no_duplicate": "name",
            },
            "resource": {
                "type": "array",
                "item_fields": {
                    "key": {"type": "string", "enum": {"flavor_type", "device_distributed_mode", "host_distributed_mode"}, "required": True},
                    "operator": {"type": "string", "enum": {"in"}, "required": True},
                    "values": {"type": "array_of_string", "enum": VALID_RESOURCE_VALUES, "required": True},
                },
            },
            "yaml_config": {"type": "string"},
        },
    },
    "image": {
        "required_fields": ["arch", "device_type"],
        "fields": {
            "arch": {"type": "string", "enum": VALID_ARCH_TYPES},
            "device_type": {"type": "array_of_string", "enum": VALID_DEVICE_TYPES},
        },
    },
    "simulation": {
        "required_fields": [],
        "sub_type_rules": {
            "robot": {
                "required_fields": ["robot_type", "robot_manufacturer"],
                "fields": {
                    "robot_type": {"type": "string", "enum": VALID_ROBOT_TYPES},
                    "robot_manufacturer": {"type": "string", "pattern": ROBOT_MANUFACTURER_PATTERN,
                                           "pattern_desc": "1-64 chars: Chinese, English, digits, hyphen, dot, underscore, space"},
                },
            },
        },
    },
}
