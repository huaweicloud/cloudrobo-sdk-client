# ============================================================================
# 本文件由 scripts/gen_schemas.py 从根 pilot-manager.yaml 自动生成。
# 存在根 pilot-manager.yaml 时，以根为准（各包 robo-operations.yaml 是其分发视图，
# 非权威）。请勿手动修改；如需改字段约束，请改根 yaml 后重新生成。
# 数据键语义：type/enum/minimum/maximum/min_length/max_length/max_items/min_items/
#            max_properties/min_properties/pattern/required/required_fields/
#            item_fields/format；A1 元信息键 source/source_doc。
# ============================================================================


from typing import Any, Dict

CREATEDISPATCHERTASKREQUESTBODY_RULES: Dict[str, Any] = {
    'fields': {
        'constraints': {
            'fields': {
                'exec_constraints': {
                    'fields': {
                        'max_iter_num': {
                            'maximum': 300000,
                            'minimum': 1,
                            'source': 'CreateDispatcherTaskRequestBody.constraints.exec_constraints.max_iter_num',
                            'source_doc': '最大推理步数，默认100步',
                            'type': 'integer'
                        },
                        'max_run_time': {
                            'maximum': 300,
                            'minimum': 1,
                            'source': 'CreateDispatcherTaskRequestBody.constraints.exec_constraints.max_run_time',
                            'source_doc': '最大推理时间，单位分钟，默认10分钟',
                            'type': 'integer'
                        }
                    },
                    'source': 'CreateDispatcherTaskRequestBody.constraints.exec_constraints',
                    'source_doc': '停止条件',
                    'type': 'object'
                },
                'model': {
                    'fields': {
                        'exec_model_id': {
                            'max_length': 64,
                            'min_length': 1,
                            'pattern': '^(?:ext_[0-9a-f]{32}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})$',
                            'pattern_desc': '执行模型ID',
                            'required': True,
                            'source': 'CreateDispatcherTaskRequestBody.constraints.model.exec_model_id',
                            'source_doc': '执行模型ID',
                            'type': 'string'
                        }
                    },
                    'required': True,
                    'required_fields': [
                        'exec_model_id'
                    ],
                    'source': 'CreateDispatcherTaskRequestBody.constraints.model',
                    'source_doc': '执行模型对象，包含执行模型ID，可选检查模型ID',
                    'type': 'object'
                },
                'robot_id': {
                    'max_length': 64,
                    'min_length': 1,
                    'pattern': '^[0-9a-f]{32}$',
                    'pattern_desc': '机器人ID',
                    'required': True,
                    'source': 'CreateDispatcherTaskRequestBody.constraints.robot_id',
                    'source_doc': '机器人ID',
                    'type': 'string'
                }
            },
            'required': True,
            'required_fields': [
                'model',
                'robot_id'
            ],
            'source': 'CreateDispatcherTaskRequestBody.constraints',
            'source_doc': '任务约束',
            'type': 'object'
        },
        'name': {
            'max_length': 1024,
            'min_length': 1,
            'required': True,
            'source': 'CreateDispatcherTaskRequestBody.name',
            'source_doc': '任务名称',
            'type': 'string'
        },
        'task': {
            'max_length': 1024,
            'min_length': 1,
            'required': True,
            'source': 'CreateDispatcherTaskRequestBody.task',
            'source_doc': '任务描述',
            'type': 'string'
        }
    },
    'required_fields': [
        'constraints',
        'name',
        'task'
    ],
    'source': 'pilot-manager.yaml#/components/schemas/CreateDispatcherTaskRequestBody',
    'source_doc': '创建任务请求参数',
    'type': 'object'
}

DISPATCHERTASKCONSTRAINTSREQUESTBODY_RULES: Dict[str, Any] = {
    'fields': {
        'exec_constraints': {
            'fields': {
                'max_iter_num': {
                    'maximum': 300000,
                    'minimum': 1,
                    'source': 'DispatcherTaskConstraintsRequestBody.exec_constraints.max_iter_num',
                    'source_doc': '最大推理步数，默认100步',
                    'type': 'integer'
                },
                'max_run_time': {
                    'maximum': 300,
                    'minimum': 1,
                    'source': 'DispatcherTaskConstraintsRequestBody.exec_constraints.max_run_time',
                    'source_doc': '最大推理时间，单位分钟，默认10分钟',
                    'type': 'integer'
                }
            },
            'source': 'DispatcherTaskConstraintsRequestBody.exec_constraints',
            'source_doc': '停止条件',
            'type': 'object'
        },
        'model': {
            'fields': {
                'exec_model_id': {
                    'max_length': 64,
                    'min_length': 1,
                    'pattern': '^(?:ext_[0-9a-f]{32}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})$',
                    'pattern_desc': '执行模型ID',
                    'required': True,
                    'source': 'DispatcherTaskConstraintsRequestBody.model.exec_model_id',
                    'source_doc': '执行模型ID',
                    'type': 'string'
                }
            },
            'required': True,
            'required_fields': [
                'exec_model_id'
            ],
            'source': 'DispatcherTaskConstraintsRequestBody.model',
            'source_doc': '执行模型对象，包含执行模型ID，可选检查模型ID',
            'type': 'object'
        },
        'robot_id': {
            'max_length': 64,
            'min_length': 1,
            'pattern': '^[0-9a-f]{32}$',
            'pattern_desc': '机器人ID',
            'required': True,
            'source': 'DispatcherTaskConstraintsRequestBody.robot_id',
            'source_doc': '机器人ID',
            'type': 'string'
        }
    },
    'required_fields': [
        'model',
        'robot_id'
    ],
    'source': 'pilot-manager.yaml#/components/schemas/DispatcherTaskConstraintsRequestBody',
    'source_doc': '任务约束',
    'type': 'object'
}

EXECMODELREQUESTBODY_RULES: Dict[str, Any] = {
    'fields': {
        'exec_model_id': {
            'max_length': 64,
            'min_length': 1,
            'pattern': '^(?:ext_[0-9a-f]{32}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})$',
            'pattern_desc': '执行模型ID',
            'required': True,
            'source': 'ExecModelRequestBody.exec_model_id',
            'source_doc': '执行模型ID',
            'type': 'string'
        }
    },
    'required_fields': [
        'exec_model_id'
    ],
    'source': 'pilot-manager.yaml#/components/schemas/ExecModelRequestBody',
    'source_doc': '执行模型对象，包含执行模型ID，可选检查模型ID',
    'type': 'object'
}

EXECCONSTRAINTS_RULES: Dict[str, Any] = {
    'fields': {
        'max_iter_num': {
            'maximum': 300000,
            'minimum': 1,
            'source': 'ExecConstraints.max_iter_num',
            'source_doc': '最大推理步数，默认100步',
            'type': 'integer'
        },
        'max_run_time': {
            'maximum': 300,
            'minimum': 1,
            'source': 'ExecConstraints.max_run_time',
            'source_doc': '最大推理时间，单位分钟，默认10分钟',
            'type': 'integer'
        }
    },
    'source': 'pilot-manager.yaml#/components/schemas/ExecConstraints',
    'source_doc': '执行技能的约束',
    'type': 'object'
}

PATH_PARAM_RULES: Dict[str, Dict[str, Any]] = {
    'session_id': {
        'max_length': 64,
        'min_length': 1,
        'pattern': '^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$',
        'required': True,
        'source_doc': '会话唯一标识ID',
        'type': 'string'
    },
    'task_id': {
        'max_length': 64,
        'min_length': 1,
        'pattern': '^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$',
        'required': True,
        'source_doc': '任务唯一标识ID',
        'type': 'string'
    }
}

QUERY_PARAM_RULES: Dict[str, Dict[str, Any]] = {
    'content_match': {
        'max_length': 1024,
        'min_length': 0,
        'required': False,
        'source_doc': '技能prompt或服务名称模糊搜索内容',
        'type': 'string'
    },
    'end_time': {
        'maximum': 32503680000000,
        'minimum': 0,
        'required': False,
        'source_doc': '按起止时间筛选，执行日志结束时间，UTC时间戳，单位毫秒',
        'type': 'integer'
    },
    'infer_service_id': {
        'max_length': 64,
        'min_length': 1,
        'required': False,
        'source_doc': '推理服务id',
        'type': 'string'
    },
    'inverse': {
        'default': False,
        'required': False,
        'source_doc': '倒置，如果为true，则倒序查询，此时offset为0代表最后一个字节',
        'type': 'boolean'
    },
    'robot_id': {
        'max_length': 64,
        'min_length': 1,
        'required': False,
        'source_doc': '机器人id',
        'type': 'string'
    },
    'sort_dir': {
        'default': 'DESC',
        'enum': [
            'ASC',
            'DESC'
        ],
        'max_length': 8,
        'min_length': 0,
        'required': False,
        'source_doc': '结果排序方式。支持DESC(desc)，ASC(asc)，默认值DESC。',
        'type': 'string'
    },
    'sort_key': {
        'default': 'updated_at',
        'enum': [
            'created_at',
            'updated_at',
            'create_at',
            'update_at'
        ],
        'max_length': 32,
        'min_length': 0,
        'required': False,
        'source_doc': '排序字段，支持created_at, updated_at, create_at, update_at，默认值updated_at。',
        'type': 'string'
    },
    'start_time': {
        'maximum': 32503680000000,
        'minimum': 0,
        'required': False,
        'source_doc': '按起止时间筛选，执行日志开始时间，UTC时间戳，单位毫秒',
        'type': 'integer'
    },
    'status': {
        'enum': [
            'RUNNING',
            'COMPLETED',
            'FAILED',
            'CANCELLED'
        ],
        'required': False,
        'source_doc': '根据执行状态查询相关日志。',
        'type': 'string'
    }
}

