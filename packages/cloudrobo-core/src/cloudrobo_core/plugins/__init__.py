import importlib.metadata
import logging

import click

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "cloudrobo.groups"


class PluginGroup(click.Group):
    """Click Group that dynamically loads commands from entry points."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._loaded = False

    def _load_plugins(self):
        if self._loaded:
            return
        self._loaded = True

        try:
            eps = importlib.metadata.entry_points()
            if hasattr(eps, "select"):
                plugin_eps = eps.select(group=ENTRY_POINT_GROUP)
            else:
                plugin_eps = eps.get(ENTRY_POINT_GROUP, [])

            for ep in plugin_eps:
                try:
                    cmd = ep.load()
                    if isinstance(cmd, click.Group):
                        self.add_command(cmd, name=ep.name)
                        logger.debug("Loaded plugin command: %s", ep.name)
                    else:
                        logger.warning("Entry point %s is not a click.Group", ep.name)
                except Exception as e:
                    logger.error("Failed to load entry point %s: %s", ep.name, e)
        except Exception as e:
            logger.error("Failed to load plugins: %s", e)

    def get_command(self, ctx, cmd_name):
        self._load_plugins()
        return super().get_command(ctx, cmd_name)

    def list_commands(self, ctx):
        self._load_plugins()
        return super().list_commands(ctx)
