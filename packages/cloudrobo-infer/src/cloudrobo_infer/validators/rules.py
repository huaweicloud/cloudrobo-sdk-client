# ============================================================================
# 本文件由 scripts/gen_schemas.py 从根 pilot-manager.yaml 自动生成。
# 存在根 pilot-manager.yaml 时，以根为准（各包 robo-operations.yaml 是其分发视图，
# 非权威）。请勿手动修改；如需改字段约束，请改根 yaml 后重新生成。
# 数据键语义：type/enum/minimum/maximum/min_length/max_length/max_items/min_items/
#            max_properties/min_properties/pattern/required/required_fields/
#            item_fields/format；A1 元信息键 source/source_doc。
# ============================================================================


from typing import Any, Dict

CREATEINFERENCESERVICEREQUESTBODY_RULES: Dict[str, Any] = {
    'fields': {
        'cmd': {
            'max_length': 1024,
            'min_length': 0,
            'source': 'CreateInferenceServiceRequestBody.cmd',
            'source_doc': '启动命令',
            'type': 'string'
        },
        'deploy_timeout_minutes': {
            'maximum': 300,
            'minimum': 1,
            'source': 'CreateInferenceServiceRequestBody.deploy_timeout_minutes',
            'source_doc': '部署超时时间',
            'type': 'integer'
        },
        'description': {
            'max_length': 512,
            'min_length': 0,
            'pattern': '^[\\s\\S]{0,512}$',
            'pattern_desc': '描述',
            'source': 'CreateInferenceServiceRequestBody.description',
            'source_doc': '描述',
            'type': 'string'
        },
        'envs': {
            'max_properties': 100,
            'source': 'CreateInferenceServiceRequestBody.envs',
            'source_doc': '环境变量',
            'type': 'object'
        },
        'files': {
            'item_fields': {
                'address': {
                    'max_length': 512,
                    'min_length': 1,
                    'pattern': '^\\/(?![\\s\\.]+$)[\\w\\-\\.\\/\\:@\\$]+$',
                    'pattern_desc': '存储地址',
                    'source': 'CreateInferenceServiceRequestBody.item.address',
                    'source_doc': '存储地址',
                    'type': 'string'
                },
                'host_cache': {
                    'source': 'CreateInferenceServiceRequestBody.item.host_cache',
                    'source_doc': '主机缓存',
                    'type': 'boolean'
                },
                'mount_path': {
                    'max_length': 512,
                    'min_length': 1,
                    'pattern': '^\\/(?![\\s\\.]+$)[\\w\\-\\.\\/\\:@\\$]+$',
                    'pattern_desc': '挂载路径',
                    'source': 'CreateInferenceServiceRequestBody.item.mount_path',
                    'source_doc': '挂载路径',
                    'type': 'string'
                },
                'os_warm_up': {
                    'source': 'CreateInferenceServiceRequestBody.item.os_warm_up',
                    'source_doc': '系统预热',
                    'type': 'boolean'
                },
                'source': {
                    'enum': [
                        'OBS'
                    ],
                    'max_length': 16,
                    'min_length': 1,
                    'source': 'CreateInferenceServiceRequestBody.item.source',
                    'source_doc': '存储类型：OBS-对象存储',
                    'type': 'string'
                }
            },
            'max_items': 10,
            'min_items': 0,
            'source': 'CreateInferenceServiceRequestBody.files',
            'source_doc': '文件挂载',
            'type': 'array'
        },
        'flavor': {
            'max_length': 64,
            'min_length': 1,
            'required': True,
            'source': 'CreateInferenceServiceRequestBody.flavor',
            'source_doc': '资源规格',
            'type': 'string'
        },
        'image_swr_url': {
            'max_length': 1024,
            'min_length': 0,
            'pattern': '^swr\\.[a-z0-9-]+\\.myhuaweicloud\\.com/.+$',
            'pattern_desc': '镜像地址',
            'source': 'CreateInferenceServiceRequestBody.image_swr_url',
            'source_doc': '镜像地址',
            'type': 'string'
        },
        'internet_access_enable': {
            'default': False,
            'source': 'CreateInferenceServiceRequestBody.internet_access_enable',
            'source_doc': '是否开启公网访问',
            'type': 'boolean'
        },
        'liveness_health': {
            'fields': {
                'check_method': {
                    'enum': [
                        'HTTP',
                        'EXEC'
                    ],
                    'max_length': 10,
                    'min_length': 1,
                    'source': 'CreateInferenceServiceRequestBody.liveness_health.check_method',
                    'source_doc': '检查方法必须是EXEC/HTTP',
                    'type': 'string'
                },
                'cmd': {
                    'max_length': 1024,
                    'min_length': 0,
                    'pattern': '^[^#~\\^\\$\\|%&*<>\\(\\)\'"\\[\\]\\{\\}]{0,1024}$',
                    'pattern_desc': '检查命令',
                    'source': 'CreateInferenceServiceRequestBody.liveness_health.cmd',
                    'source_doc': '检查命令',
                    'type': 'string'
                },
                'failure_threshold': {
                    'maximum': 2147483647,
                    'minimum': 1,
                    'source': 'CreateInferenceServiceRequestBody.liveness_health.failure_threshold',
                    'source_doc': '失败检查次数',
                    'type': 'integer'
                },
                'initial_delay_seconds': {
                    'maximum': 2147483647,
                    'minimum': 1,
                    'source': 'CreateInferenceServiceRequestBody.liveness_health.initial_delay_seconds',
                    'source_doc': '初次检查延迟时长',
                    'type': 'integer'
                },
                'period_seconds': {
                    'maximum': 2147483647,
                    'minimum': 1,
                    'source': 'CreateInferenceServiceRequestBody.liveness_health.period_seconds',
                    'source_doc': '检查周期时长',
                    'type': 'integer'
                },
                'protocol': {
                    'enum': [
                        'HTTP',
                        'HTTPS'
                    ],
                    'source': 'CreateInferenceServiceRequestBody.liveness_health.protocol',
                    'source_doc': '协议类型必须是HTTP/HTTPS',
                    'type': 'string'
                },
                'timeout_seconds': {
                    'maximum': 2147483647,
                    'minimum': 1,
                    'source': 'CreateInferenceServiceRequestBody.liveness_health.timeout_seconds',
                    'source_doc': '超时时长',
                    'type': 'integer'
                },
                'url': {
                    'max_length': 1024,
                    'min_length': 1,
                    'pattern': '^/[A-Za-z0-9\\-_:\\/]{0,1023}$',
                    'pattern_desc': '检查请求url',
                    'source': 'CreateInferenceServiceRequestBody.liveness_health.url',
                    'source_doc': '检查请求url',
                    'type': 'string'
                }
            },
            'source': 'CreateInferenceServiceRequestBody.liveness_health',
            'source_doc': '模型服务健康检查',
            'type': 'object'
        },
        'model': {
            'fields': {
                'model_id': {
                    'max_length': 64,
                    'min_length': 1,
                    'required': True,
                    'source': 'CreateInferenceServiceRequestBody.model.model_id',
                    'source_doc': '模型ID',
                    'type': 'string'
                },
                'model_version_id': {
                    'max_length': 64,
                    'min_length': 1,
                    'required': True,
                    'source': 'CreateInferenceServiceRequestBody.model.model_version_id',
                    'source_doc': '模型版本ID',
                    'type': 'string'
                },
                'mount_path': {
                    'max_length': 512,
                    'min_length': 1,
                    'pattern': '^\\/(?![\\s\\.]+$)[\\w\\-\\.\\/\\:@\\$]+$',
                    'pattern_desc': '挂载路径',
                    'source': 'CreateInferenceServiceRequestBody.model.mount_path',
                    'source_doc': '挂载路径',
                    'type': 'string'
                }
            },
            'required': True,
            'required_fields': [
                'model_id',
                'model_version_id'
            ],
            'source': 'CreateInferenceServiceRequestBody.model',
            'source_doc': '模型信息',
            'type': 'object'
        },
        'model_ext_metadata': {
            'format': 'json',
            'json_like': True,
            'max_length': 20480,
            'min_length': 0,
            'pattern_desc': '模型扩展元数据，JSON/YAML 格式的 r2c 配置信息。',
            'source': 'CreateInferenceServiceRequestBody.model_ext_metadata',
            'source_doc': '模型扩展元数据，JSON/YAML 格式的 r2c 配置信息。',
            'type': 'string'
        },
        'name': {
            'max_length': 64,
            'min_length': 3,
            'pattern': '^[\\u4e00-\\u9fa5a-zA-Z0-9_\\-./]{3,64}$',
            'pattern_desc': '服务名称',
            'required': True,
            'source': 'CreateInferenceServiceRequestBody.name',
            'source_doc': '服务名称',
            'type': 'string'
        },
        'pool_id': {
            'max_length': 64,
            'min_length': 0,
            'required': True,
            'source': 'CreateInferenceServiceRequestBody.pool_id',
            'source_doc': '资源池ID',
            'type': 'string'
        },
        'pool_type': {
            'enum': [
                'SHARED',
                'DEDICATED'
            ],
            'max_length': 16,
            'min_length': 0,
            'required': True,
            'source': 'CreateInferenceServiceRequestBody.pool_type',
            'source_doc': '资源池类型',
            'type': 'string'
        },
        'readiness_health': {
            'fields': {
                'check_method': {
                    'enum': [
                        'HTTP',
                        'EXEC'
                    ],
                    'max_length': 10,
                    'min_length': 1,
                    'source': 'CreateInferenceServiceRequestBody.readiness_health.check_method',
                    'source_doc': '检查方法必须是EXEC/HTTP',
                    'type': 'string'
                },
                'cmd': {
                    'max_length': 1024,
                    'min_length': 0,
                    'pattern': '^[^#~\\^\\$\\|%&*<>\\(\\)\'"\\[\\]\\{\\}]{0,1024}$',
                    'pattern_desc': '检查命令',
                    'source': 'CreateInferenceServiceRequestBody.readiness_health.cmd',
                    'source_doc': '检查命令',
                    'type': 'string'
                },
                'failure_threshold': {
                    'maximum': 2147483647,
                    'minimum': 1,
                    'source': 'CreateInferenceServiceRequestBody.readiness_health.failure_threshold',
                    'source_doc': '失败检查次数',
                    'type': 'integer'
                },
                'initial_delay_seconds': {
                    'maximum': 2147483647,
                    'minimum': 1,
                    'source': 'CreateInferenceServiceRequestBody.readiness_health.initial_delay_seconds',
                    'source_doc': '初次检查延迟时长',
                    'type': 'integer'
                },
                'period_seconds': {
                    'maximum': 2147483647,
                    'minimum': 1,
                    'source': 'CreateInferenceServiceRequestBody.readiness_health.period_seconds',
                    'source_doc': '检查周期时长',
                    'type': 'integer'
                },
                'protocol': {
                    'enum': [
                        'HTTP',
                        'HTTPS'
                    ],
                    'source': 'CreateInferenceServiceRequestBody.readiness_health.protocol',
                    'source_doc': '协议类型必须是HTTP/HTTPS',
                    'type': 'string'
                },
                'timeout_seconds': {
                    'maximum': 2147483647,
                    'minimum': 1,
                    'source': 'CreateInferenceServiceRequestBody.readiness_health.timeout_seconds',
                    'source_doc': '超时时长',
                    'type': 'integer'
                },
                'url': {
                    'max_length': 1024,
                    'min_length': 1,
                    'pattern': '^/[A-Za-z0-9\\-_:\\/]{0,1023}$',
                    'pattern_desc': '检查请求url',
                    'source': 'CreateInferenceServiceRequestBody.readiness_health.url',
                    'source_doc': '检查请求url',
                    'type': 'string'
                }
            },
            'source': 'CreateInferenceServiceRequestBody.readiness_health',
            'source_doc': '模型服务健康检查',
            'type': 'object'
        },
        'service_invoke': {
            'fields': {
                'auth_type': {
                    'enum': [
                        'API_KEY',
                        'NONE'
                    ],
                    'max_length': 16,
                    'min_length': 1,
                    'required': True,
                    'source': 'CreateInferenceServiceRequestBody.service_invoke.auth_type',
                    'source_doc': '认证类型：API_KEY-密钥认证，NONE-无认证',
                    'type': 'string'
                },
                'port': {
                    'maximum': 65535,
                    'minimum': 1024,
                    'required': True,
                    'source': 'CreateInferenceServiceRequestBody.service_invoke.port',
                    'source_doc': '端口',
                    'type': 'integer'
                },
                'protocol': {
                    'enum': [
                        'HTTP',
                        'HTTPS',
                        'WS',
                        'WSS'
                    ],
                    'max_length': 8,
                    'min_length': 1,
                    'required': True,
                    'source': 'CreateInferenceServiceRequestBody.service_invoke.protocol',
                    'source_doc': '协议：HTTP-http协议，HTTPS-https协议，WS-websocket协议，WSS-websockets协议',
                    'type': 'string'
                }
            },
            'required_fields': [
                'auth_type',
                'port',
                'protocol'
            ],
            'source': 'CreateInferenceServiceRequestBody.service_invoke',
            'source_doc': '服务调用配置',
            'type': 'object'
        },
        'skill_config': {
            'fields': {
                'skills': {
                    'item_fields': {
                        'name': {
                            'max_length': 64,
                            'min_length': 1,
                            'pattern': '(?!^\\s)[\\u4e00-\\u9fa5a-zA-Z0-9-_\\s]{1,64}(?<!\\s)$',
                            'pattern_desc': '技能名称',
                            'required': True,
                            'source': 'CreateInferenceServiceRequestBody.skill_config.item.name',
                            'source_doc': '技能名称',
                            'type': 'string'
                        },
                        'prompt': {
                            'max_length': 1024,
                            'min_length': 1,
                            'required': True,
                            'source': 'CreateInferenceServiceRequestBody.skill_config.item.prompt',
                            'source_doc': '技能提示词',
                            'type': 'string'
                        }
                    },
                    'item_required_fields': [
                        'name',
                        'prompt'
                    ],
                    'max_items': 50,
                    'min_items': 0,
                    'source': 'CreateInferenceServiceRequestBody.skill_config.skills',
                    'source_doc': '技能列表',
                    'type': 'array'
                },
                'strict': {
                    'source': 'CreateInferenceServiceRequestBody.skill_config.strict',
                    'source_doc': '是否严格匹配',
                    'type': 'boolean'
                }
            },
            'source': 'CreateInferenceServiceRequestBody.skill_config',
            'source_doc': '技能配置',
            'type': 'object'
        },
        'startup_health': {
            'fields': {
                'check_method': {
                    'enum': [
                        'HTTP',
                        'EXEC'
                    ],
                    'max_length': 10,
                    'min_length': 1,
                    'source': 'CreateInferenceServiceRequestBody.startup_health.check_method',
                    'source_doc': '检查方法必须是EXEC/HTTP',
                    'type': 'string'
                },
                'cmd': {
                    'max_length': 1024,
                    'min_length': 0,
                    'pattern': '^[^#~\\^\\$\\|%&*<>\\(\\)\'"\\[\\]\\{\\}]{0,1024}$',
                    'pattern_desc': '检查命令',
                    'source': 'CreateInferenceServiceRequestBody.startup_health.cmd',
                    'source_doc': '检查命令',
                    'type': 'string'
                },
                'failure_threshold': {
                    'maximum': 2147483647,
                    'minimum': 1,
                    'source': 'CreateInferenceServiceRequestBody.startup_health.failure_threshold',
                    'source_doc': '失败检查次数',
                    'type': 'integer'
                },
                'initial_delay_seconds': {
                    'maximum': 2147483647,
                    'minimum': 1,
                    'source': 'CreateInferenceServiceRequestBody.startup_health.initial_delay_seconds',
                    'source_doc': '初次检查延迟时长',
                    'type': 'integer'
                },
                'period_seconds': {
                    'maximum': 2147483647,
                    'minimum': 1,
                    'source': 'CreateInferenceServiceRequestBody.startup_health.period_seconds',
                    'source_doc': '检查周期时长',
                    'type': 'integer'
                },
                'protocol': {
                    'enum': [
                        'HTTP',
                        'HTTPS'
                    ],
                    'source': 'CreateInferenceServiceRequestBody.startup_health.protocol',
                    'source_doc': '协议类型必须是HTTP/HTTPS',
                    'type': 'string'
                },
                'timeout_seconds': {
                    'maximum': 2147483647,
                    'minimum': 1,
                    'source': 'CreateInferenceServiceRequestBody.startup_health.timeout_seconds',
                    'source_doc': '超时时长',
                    'type': 'integer'
                },
                'url': {
                    'max_length': 1024,
                    'min_length': 1,
                    'pattern': '^/[A-Za-z0-9\\-_:\\/]{0,1023}$',
                    'pattern_desc': '检查请求url',
                    'source': 'CreateInferenceServiceRequestBody.startup_health.url',
                    'source_doc': '检查请求url',
                    'type': 'string'
                }
            },
            'source': 'CreateInferenceServiceRequestBody.startup_health',
            'source_doc': '模型服务健康检查',
            'type': 'object'
        },
        'stop_schedule': {
            'fields': {
                'duration': {
                    'maximum': 10080,
                    'minimum': 1,
                    'required': True,
                    'source': 'CreateInferenceServiceRequestBody.stop_schedule.duration',
                    'source_doc': '时长',
                    'type': 'integer'
                },
                'time_unit': {
                    'enum': [
                        'MINUTES',
                        'HOURS',
                        'DAYS'
                    ],
                    'max_length': 16,
                    'min_length': 1,
                    'required': True,
                    'source': 'CreateInferenceServiceRequestBody.stop_schedule.time_unit',
                    'source_doc': '单位：MINUTES-分钟，HOURS-小时，DAYS-天',
                    'type': 'string'
                }
            },
            'required_fields': [
                'duration',
                'time_unit'
            ],
            'source': 'CreateInferenceServiceRequestBody.stop_schedule',
            'source_doc': '定时停止配置',
            'type': 'object'
        },
        'workspace_id': {
            'max_length': 64,
            'min_length': 1,
            'required': True,
            'source': 'CreateInferenceServiceRequestBody.workspace_id',
            'source_doc': '工作空间ID',
            'type': 'string'
        }
    },
    'required_fields': [
        'flavor',
        'model',
        'name',
        'pool_id',
        'pool_type',
        'workspace_id'
    ],
    'source': 'pilot-manager.yaml#/components/schemas/CreateInferenceServiceRequestBody',
    'source_doc': '创建推理服务请求',
    'type': 'object'
}

MODELINFO_RULES: Dict[str, Any] = {
    'fields': {
        'model_id': {
            'max_length': 64,
            'min_length': 1,
            'required': True,
            'source': 'ModelInfo.model_id',
            'source_doc': '模型ID',
            'type': 'string'
        },
        'model_version_id': {
            'max_length': 64,
            'min_length': 1,
            'required': True,
            'source': 'ModelInfo.model_version_id',
            'source_doc': '模型版本ID',
            'type': 'string'
        },
        'mount_path': {
            'max_length': 512,
            'min_length': 1,
            'pattern': '^\\/(?![\\s\\.]+$)[\\w\\-\\.\\/\\:@\\$]+$',
            'pattern_desc': '挂载路径',
            'source': 'ModelInfo.mount_path',
            'source_doc': '挂载路径',
            'type': 'string'
        }
    },
    'required_fields': [
        'model_id',
        'model_version_id'
    ],
    'source': 'pilot-manager.yaml#/components/schemas/ModelInfo',
    'source_doc': '模型信息',
    'type': 'object'
}

SCHEDULECONFIG_RULES: Dict[str, Any] = {
    'fields': {
        'duration': {
            'maximum': 10080,
            'minimum': 1,
            'required': True,
            'source': 'ScheduleConfig.duration',
            'source_doc': '时长',
            'type': 'integer'
        },
        'time_unit': {
            'enum': [
                'MINUTES',
                'HOURS',
                'DAYS'
            ],
            'max_length': 16,
            'min_length': 1,
            'required': True,
            'source': 'ScheduleConfig.time_unit',
            'source_doc': '单位：MINUTES-分钟，HOURS-小时，DAYS-天',
            'type': 'string'
        }
    },
    'required_fields': [
        'duration',
        'time_unit'
    ],
    'source': 'pilot-manager.yaml#/components/schemas/ScheduleConfig',
    'source_doc': '定时停止配置',
    'type': 'object'
}

SERVICEINVOKE_RULES: Dict[str, Any] = {
    'fields': {
        'auth_type': {
            'enum': [
                'API_KEY',
                'NONE'
            ],
            'max_length': 16,
            'min_length': 1,
            'required': True,
            'source': 'ServiceInvoke.auth_type',
            'source_doc': '认证类型：API_KEY-密钥认证，NONE-无认证',
            'type': 'string'
        },
        'port': {
            'maximum': 65535,
            'minimum': 1024,
            'required': True,
            'source': 'ServiceInvoke.port',
            'source_doc': '端口',
            'type': 'integer'
        },
        'protocol': {
            'enum': [
                'HTTP',
                'HTTPS',
                'WS',
                'WSS'
            ],
            'max_length': 8,
            'min_length': 1,
            'required': True,
            'source': 'ServiceInvoke.protocol',
            'source_doc': '协议：HTTP-http协议，HTTPS-https协议，WS-websocket协议，WSS-websockets协议',
            'type': 'string'
        }
    },
    'required_fields': [
        'auth_type',
        'port',
        'protocol'
    ],
    'source': 'pilot-manager.yaml#/components/schemas/ServiceInvoke',
    'source_doc': '服务调用配置',
    'type': 'object'
}

SKILLCONFIG_RULES: Dict[str, Any] = {
    'fields': {
        'skills': {
            'item_fields': {
                'name': {
                    'max_length': 64,
                    'min_length': 1,
                    'pattern': '(?!^\\s)[\\u4e00-\\u9fa5a-zA-Z0-9-_\\s]{1,64}(?<!\\s)$',
                    'pattern_desc': '技能名称',
                    'required': True,
                    'source': 'SkillConfig.item.name',
                    'source_doc': '技能名称',
                    'type': 'string'
                },
                'prompt': {
                    'max_length': 1024,
                    'min_length': 1,
                    'required': True,
                    'source': 'SkillConfig.item.prompt',
                    'source_doc': '技能提示词',
                    'type': 'string'
                }
            },
            'item_required_fields': [
                'name',
                'prompt'
            ],
            'max_items': 50,
            'min_items': 0,
            'source': 'SkillConfig.skills',
            'source_doc': '技能列表',
            'type': 'array'
        },
        'strict': {
            'source': 'SkillConfig.strict',
            'source_doc': '是否严格匹配',
            'type': 'boolean'
        }
    },
    'source': 'pilot-manager.yaml#/components/schemas/SkillConfig',
    'source_doc': '技能配置',
    'type': 'object'
}

FILEMOUNT_RULES: Dict[str, Any] = {
    'fields': {
        'address': {
            'max_length': 512,
            'min_length': 1,
            'pattern': '^\\/(?![\\s\\.]+$)[\\w\\-\\.\\/\\:@\\$]+$',
            'pattern_desc': '存储地址',
            'source': 'FileMount.address',
            'source_doc': '存储地址',
            'type': 'string'
        },
        'host_cache': {
            'source': 'FileMount.host_cache',
            'source_doc': '主机缓存',
            'type': 'boolean'
        },
        'mount_path': {
            'max_length': 512,
            'min_length': 1,
            'pattern': '^\\/(?![\\s\\.]+$)[\\w\\-\\.\\/\\:@\\$]+$',
            'pattern_desc': '挂载路径',
            'source': 'FileMount.mount_path',
            'source_doc': '挂载路径',
            'type': 'string'
        },
        'os_warm_up': {
            'source': 'FileMount.os_warm_up',
            'source_doc': '系统预热',
            'type': 'boolean'
        },
        'source': {
            'enum': [
                'OBS'
            ],
            'max_length': 16,
            'min_length': 1,
            'source': 'FileMount.source',
            'source_doc': '存储类型：OBS-对象存储',
            'type': 'string'
        }
    },
    'source': 'pilot-manager.yaml#/components/schemas/FileMount',
    'source_doc': '文件挂载',
    'type': 'object'
}

MODELEXTMETADATA_RULES: Dict[str, Any] = {
    'format': 'json',
    'json_like': True,
    'max_length': 20480,
    'min_length': 0,
    'pattern_desc': '模型扩展元数据，JSON/YAML 格式的 r2c 配置信息。',
    'source': 'pilot-manager.yaml#/components/schemas/ModelExtMetadata',
    'source_doc': '模型扩展元数据，JSON/YAML 格式的 r2c 配置信息。',
    'type': 'string'
}

HEALTHCHECK_RULES: Dict[str, Any] = {
    'fields': {
        'check_method': {
            'enum': [
                'HTTP',
                'EXEC'
            ],
            'max_length': 10,
            'min_length': 1,
            'source': 'HealthCheck.check_method',
            'source_doc': '检查方法必须是EXEC/HTTP',
            'type': 'string'
        },
        'cmd': {
            'max_length': 1024,
            'min_length': 0,
            'pattern': '^[^#~\\^\\$\\|%&*<>\\(\\)\'"\\[\\]\\{\\}]{0,1024}$',
            'pattern_desc': '检查命令',
            'source': 'HealthCheck.cmd',
            'source_doc': '检查命令',
            'type': 'string'
        },
        'failure_threshold': {
            'maximum': 2147483647,
            'minimum': 1,
            'source': 'HealthCheck.failure_threshold',
            'source_doc': '失败检查次数',
            'type': 'integer'
        },
        'initial_delay_seconds': {
            'maximum': 2147483647,
            'minimum': 1,
            'source': 'HealthCheck.initial_delay_seconds',
            'source_doc': '初次检查延迟时长',
            'type': 'integer'
        },
        'period_seconds': {
            'maximum': 2147483647,
            'minimum': 1,
            'source': 'HealthCheck.period_seconds',
            'source_doc': '检查周期时长',
            'type': 'integer'
        },
        'protocol': {
            'enum': [
                'HTTP',
                'HTTPS'
            ],
            'source': 'HealthCheck.protocol',
            'source_doc': '协议类型必须是HTTP/HTTPS',
            'type': 'string'
        },
        'timeout_seconds': {
            'maximum': 2147483647,
            'minimum': 1,
            'source': 'HealthCheck.timeout_seconds',
            'source_doc': '超时时长',
            'type': 'integer'
        },
        'url': {
            'max_length': 1024,
            'min_length': 1,
            'pattern': '^/[A-Za-z0-9\\-_:\\/]{0,1023}$',
            'pattern_desc': '检查请求url',
            'source': 'HealthCheck.url',
            'source_doc': '检查请求url',
            'type': 'string'
        }
    },
    'source': 'pilot-manager.yaml#/components/schemas/HealthCheck',
    'source_doc': '模型服务健康检查',
    'type': 'object'
}

SKILLDETAIL_RULES: Dict[str, Any] = {
    'fields': {
        'name': {
            'max_length': 64,
            'min_length': 1,
            'pattern': '(?!^\\s)[\\u4e00-\\u9fa5a-zA-Z0-9-_\\s]{1,64}(?<!\\s)$',
            'pattern_desc': '技能名称',
            'required': True,
            'source': 'SkillDetail.name',
            'source_doc': '技能名称',
            'type': 'string'
        },
        'prompt': {
            'max_length': 1024,
            'min_length': 1,
            'required': True,
            'source': 'SkillDetail.prompt',
            'source_doc': '技能提示词',
            'type': 'string'
        }
    },
    'required_fields': [
        'name',
        'prompt'
    ],
    'source': 'pilot-manager.yaml#/components/schemas/SkillDetail',
    'source_doc': '技能详情',
    'type': 'object'
}

UPDATEINFERENCESERVICEREQUESTBODY_RULES: Dict[str, Any] = {
    'fields': {
        'description': {
            'max_length': 512,
            'min_length': 0,
            'pattern': '^[\\s\\S]{0,512}$',
            'pattern_desc': '描述',
            'source': 'UpdateInferenceServiceRequestBody.description',
            'source_doc': '描述',
            'type': 'string'
        },
        'model_ext_metadata': {
            'format': 'json',
            'json_like': True,
            'max_length': 20480,
            'min_length': 0,
            'pattern_desc': '模型扩展元数据，JSON/YAML 格式的 r2c 配置信息。',
            'source': 'UpdateInferenceServiceRequestBody.model_ext_metadata',
            'source_doc': '模型扩展元数据，JSON/YAML 格式的 r2c 配置信息。',
            'type': 'string'
        }
    },
    'source': 'pilot-manager.yaml#/components/schemas/UpdateInferenceServiceRequestBody',
    'source_doc': '更新推理服务请求',
    'type': 'object'
}

LISTINFERENCESERVICELOGSREQUESTBODY_RULES: Dict[str, Any] = {
    'fields': {
        'end_time': {
            'maximum': 32503680000000,
            'minimum': 0,
            'required': True,
            'source': 'ListInferenceServiceLogsRequestBody.end_time',
            'source_doc': '搜索日志的结束时间。',
            'type': 'integer'
        },
        'highlight': {
            'source': 'ListInferenceServiceLogsRequestBody.highlight',
            'source_doc': '在查询结果中日志关键词是否高亮显示。',
            'type': 'boolean'
        },
        'is_count': {
            'source': 'ListInferenceServiceLogsRequestBody.is_count',
            'source_doc': '在查询结果中是否统计日志条数。',
            'type': 'boolean'
        },
        'is_desc': {
            'source': 'ListInferenceServiceLogsRequestBody.is_desc',
            'source_doc': '表示日志查询的顺序，当前支持顺序（false）或倒序查询（true）。',
            'type': 'boolean'
        },
        'keywords': {
            'max_length': 256,
            'min_length': 0,
            'source': 'ListInferenceServiceLogsRequestBody.keywords',
            'source_doc': '支持关键词精确搜索。关键词指相邻两个分词之间的单词。',
            'type': 'string'
        },
        'limit': {
            'maximum': 5000,
            'minimum': 1,
            'source': 'ListInferenceServiceLogsRequestBody.limit',
            'source_doc': '每次查询的日志条数。最小值：1，最大值：5000。',
            'type': 'integer'
        },
        'line_num': {
            'max_length': 128,
            'min_length': 0,
            'source': 'ListInferenceServiceLogsRequestBody.line_num',
            'source_doc': '日志单行序列号，标识日志上报顺序，通常用于分页查询和日志数据的有序处理。分页查询需要使用该参数，用于从上次查询结束的问题继续查询。该参数从上次查询的返回结果中获取。',
            'type': 'string'
        },
        'start_time': {
            'maximum': 32503680000000,
            'minimum': 0,
            'required': True,
            'source': 'ListInferenceServiceLogsRequestBody.start_time',
            'source_doc': '搜索日志的起始时间。',
            'type': 'integer'
        }
    },
    'required_fields': [
        'end_time',
        'start_time'
    ],
    'source': 'pilot-manager.yaml#/components/schemas/ListInferenceServiceLogsRequestBody',
    'source_doc': '查询推理服务日志请求体。',
    'type': 'object'
}

PATH_PARAM_RULES: Dict[str, Dict[str, Any]] = {
    'service_id': {
        'max_length': 36,
        'min_length': 1,
        'pattern': '^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$',
        'required': True,
        'source_doc': '推理服务唯一标识ID',
        'type': 'string'
    }
}

QUERY_PARAM_RULES: Dict[str, Dict[str, Any]] = {
    'contain_ext_metadata': {
        'required': False,
        'source_doc': '是否只返回包含 model_ext_metadata 的记录。省略=返回全部；true=只返回有 model_ext_metadata 的记录；false=只返回没有 model_ext_metadata 的记录',
        'type': 'boolean'
    },
    'limit': {
        'default': 10,
        'maximum': 50,
        'minimum': 1,
        'required': False,
        'source_doc': '每页数据条数',
        'type': 'integer'
    },
    'model_id': {
        'max_length': 64,
        'min_length': 0,
        'required': False,
        'source_doc': '模型资产ID筛选',
        'type': 'string'
    },
    'model_name': {
        'max_length': 64,
        'min_length': 0,
        'required': False,
        'source_doc': '模型资产名称筛选',
        'type': 'string'
    },
    'model_version_id': {
        'max_length': 64,
        'min_length': 0,
        'required': False,
        'source_doc': '模型版本ID筛选',
        'type': 'string'
    },
    'model_version_name': {
        'max_length': 64,
        'min_length': 0,
        'required': False,
        'source_doc': '模型版本名称筛选',
        'type': 'string'
    },
    'name': {
        'max_length': 64,
        'min_length': 0,
        'required': False,
        'source_doc': '推理服务名称模糊查询',
        'type': 'string'
    },
    'offset': {
        'default': 0,
        'maximum': 1000,
        'minimum': 0,
        'required': False,
        'source_doc': '分页页码偏移量',
        'type': 'integer'
    },
    'sort_dir': {
        'enum': [
            'ASC',
            'DESC'
        ],
        'required': False,
        'source_doc': '排序方向，ASC正序 / DESC倒序',
        'type': 'string'
    },
    'sort_key': {
        'enum': [
            'created_at',
            'updated_at',
            'create_at',
            'update_at'
        ],
        'max_length': 32,
        'min_length': 1,
        'required': False,
        'source_doc': '排序字段，支持 create_at / update_at / created_at / updated_at',
        'type': 'string'
    },
    'status': {
        'max_length': 128,
        'min_length': 0,
        'required': False,
        'source_doc': '根据服务状态查询相关推理服务，支持多选',
        'type': 'string'
    },
    'user_id': {
        'max_length': 64,
        'min_length': 0,
        'required': False,
        'source_doc': '创建人ID筛选',
        'type': 'string'
    },
    'user_name': {
        'max_length': 64,
        'min_length': 0,
        'required': False,
        'source_doc': '创建人名称筛选',
        'type': 'string'
    },
    'workspace_id': {
        'max_length': 64,
        'min_length': 1,
        'required': True,
        'source_doc': '工作空间ID',
        'type': 'string'
    }
}

