# ============================================================================
# 本文件由 scripts/gen_schemas.py 从根 pilot-manager.yaml 自动生成。
# 存在根 pilot-manager.yaml 时，以根为准（各包 robo-operations.yaml 是其分发视图，
# 非权威）。请勿手动修改；如需改字段约束，请改根 yaml 后重新生成。
# 数据键语义：type/enum/minimum/maximum/min_length/max_length/max_items/min_items/
#            max_properties/min_properties/pattern/required/required_fields/
#            item_fields/format；A1 元信息键 source/source_doc。
# ============================================================================


from typing import Any, Dict

CREATEROBOTREQUESTBODY_RULES: Dict[str, Any] = {
    'fields': {
        'description': {
            'max_length': 512,
            'min_length': 0,
            'pattern': '^[\\s\\S]{0,512}$',
            'pattern_desc': '描述',
            'source': 'CreateRobotRequestBody.description',
            'source_doc': '描述',
            'type': 'string'
        },
        'manufacturer': {
            'max_length': 64,
            'min_length': 1,
            'required': True,
            'source': 'CreateRobotRequestBody.manufacturer',
            'source_doc': '厂家',
            'type': 'string'
        },
        'name': {
            'max_length': 64,
            'min_length': 3,
            'pattern': '^[\\u4e00-\\u9fa5a-zA-Z0-9_\\-./]{3,64}$',
            'pattern_desc': '名称',
            'required': True,
            'source': 'CreateRobotRequestBody.name',
            'source_doc': '名称',
            'type': 'string'
        },
        'robot_model': {
            'max_length': 64,
            'min_length': 1,
            'required': True,
            'source': 'CreateRobotRequestBody.robot_model',
            'source_doc': '型号',
            'type': 'string'
        },
        'type': {
            'enum': [
                'HUMANOID',
                'QUADRUPED',
                'ARM',
                'OPERATION',
                'WHEELED',
                'OTHER'
            ],
            'max_length': 32,
            'min_length': 1,
            'required': True,
            'source': 'CreateRobotRequestBody.type',
            'source_doc': '机器人类型：HUMANOID-人形，QUADRUPED-四足，ARM-机械臂，OPERATION-复合，WHEELED-轮式，OTHER-其他',
            'type': 'string'
        },
        'workspace_id': {
            'max_length': 64,
            'min_length': 1,
            'required': True,
            'source': 'CreateRobotRequestBody.workspace_id',
            'source_doc': '工作空间ID',
            'type': 'string'
        }
    },
    'required_fields': [
        'manufacturer',
        'name',
        'robot_model',
        'type',
        'workspace_id'
    ],
    'source': 'pilot-manager.yaml#/components/schemas/CreateRobotRequestBody',
    'source_doc': '机器人注册请求参数',
    'type': 'object'
}

UPDATEROBOTREQUESTBODY_RULES: Dict[str, Any] = {
    'fields': {
        'description': {
            'max_length': 512,
            'min_length': 0,
            'pattern': '^[\\s\\S]{0,512}$',
            'pattern_desc': '描述',
            'source': 'UpdateRobotRequestBody.description',
            'source_doc': '描述',
            'type': 'string'
        },
        'name': {
            'max_length': 64,
            'min_length': 3,
            'pattern': '^[\\u4e00-\\u9fa5a-zA-Z0-9_\\-./]{3,64}$',
            'pattern_desc': '机器人名称',
            'source': 'UpdateRobotRequestBody.name',
            'source_doc': '机器人名称',
            'type': 'string'
        },
        'workspace_id': {
            'max_length': 64,
            'min_length': 1,
            'required': True,
            'source': 'UpdateRobotRequestBody.workspace_id',
            'source_doc': '当前工作空间ID，只允许上传该机器人对应的工作空间ID',
            'type': 'string'
        }
    },
    'required_fields': [
        'workspace_id'
    ],
    'source': 'pilot-manager.yaml#/components/schemas/UpdateRobotRequestBody',
    'source_doc': '更新机器人请求',
    'type': 'object'
}

EXPORTROBOTCERTIFICATEREQUESTBODY_RULES: Dict[str, Any] = {
    'fields': {
        'password': {
            'max_length': 32,
            'min_length': 0,
            'source': 'ExportRobotCertificateRequestBody.password',
            'source_doc': '机器人证书加密密码',
            'type': 'string'
        }
    },
    'source': 'pilot-manager.yaml#/components/schemas/ExportRobotCertificateRequestBody',
    'source_doc': '导出机器人证书请求体',
    'type': 'object'
}

PATH_PARAM_RULES: Dict[str, Dict[str, Any]] = {
    'robot_id': {
        'max_length': 64,
        'min_length': 1,
        'pattern': '^[0-9a-f]{32}$',
        'required': True,
        'source_doc': '机器人唯一标识ID',
        'type': 'string'
    }
}

QUERY_PARAM_RULES: Dict[str, Dict[str, Any]] = {
    'limit': {
        'maximum': 100,
        'minimum': 1,
        'required': False,
        'source_doc': '分页查询单页数据条数',
        'type': 'integer'
    },
    'manufacturer': {
        'max_length': 64,
        'min_length': 0,
        'required': False,
        'source_doc': '机器人厂家筛选',
        'type': 'string'
    },
    'name': {
        'max_length': 64,
        'min_length': 0,
        'required': False,
        'source_doc': '机器人名称模糊筛选',
        'type': 'string'
    },
    'offset': {
        'maximum': 1000,
        'minimum': 0,
        'required': False,
        'source_doc': '分页查询偏移量',
        'type': 'integer'
    },
    'robot_model': {
        'max_length': 64,
        'min_length': 0,
        'required': False,
        'source_doc': '机器人型号筛选',
        'type': 'string'
    },
    'sort': {
        'max_length': 64,
        'min_length': 0,
        'required': False,
        'source_doc': '排序规则，格式：字段:排序方式，例：created_at:desc',
        'type': 'string'
    },
    'status': {
        'max_length': 128,
        'min_length': 0,
        'required': False,
        'source_doc': '机器人状态精准筛选,支持多选',
        'type': 'string'
    },
    'type': {
        'max_length': 32,
        'min_length': 0,
        'required': False,
        'source_doc': '机器人类型筛选',
        'type': 'string'
    },
    'user_id': {
        'max_length': 32,
        'min_length': 0,
        'required': False,
        'source_doc': '用户id筛选',
        'type': 'string'
    },
    'user_name': {
        'max_length': 32,
        'min_length': 0,
        'required': False,
        'source_doc': '用户名筛选',
        'type': 'string'
    },
    'workspace_id': {
        'max_length': 64,
        'min_length': 1,
        'required': True,
        'source_doc': '工作空间唯一标识ID',
        'type': 'string'
    }
}

