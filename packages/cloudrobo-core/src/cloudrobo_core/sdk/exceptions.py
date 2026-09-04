class CloudRoboError(Exception):
    pass


class AuthenticationError(CloudRoboError):
    pass


class ResourceNotFoundError(CloudRoboError):
    pass


class ResourceConflictError(CloudRoboError):
    pass


class RateLimitError(CloudRoboError):
    def __init__(self, message="Rate limited", retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


class ServiceError(CloudRoboError):
    def __init__(self, message="Service error", status_code=None):
        super().__init__(message)
        self.status_code = status_code


class TaskFailedError(CloudRoboError):
    pass


class PathTraversalError(CloudRoboError):
    pass


class BadParameterError(CloudRoboError):
    pass


def validate_safe_id(id_value: str, name: str = "id") -> str:
    if not id_value or not isinstance(id_value, str):
        raise PathTraversalError(f"Invalid {name}: empty or not string")
    if ".." in id_value or "/" in id_value or "\\" in id_value:
        raise PathTraversalError(f"Invalid {name}: path traversal detected")
    return id_value
