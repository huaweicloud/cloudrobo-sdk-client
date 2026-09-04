import json
import os
import click
from cloudrobo_core.sdk import Config, HttpClient


def get_client(ctx, client_class):
    config_path = os.environ.get("CLOUDROBO_SERVICE_CONFIG")
    config = Config(config_path or None)
    http = HttpClient(config)
    return client_class(http)


def out(result):
    click.echo(json.dumps(result, ensure_ascii=False, indent=2))
