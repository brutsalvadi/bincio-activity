"""Top-level CLI entry point."""

import click

from bincio import __version__


@click.group()
@click.version_option(__version__)
def main() -> None:
    """BincioActivity — federated, open-source activity stats."""


from bincio.extract.cli import extract          # noqa: E402
from bincio.render.cli import render            # noqa: E402
from bincio.edit.cli import edit                # noqa: E402
from bincio.import_.cli import import_group     # noqa: E402
from bincio.serve.init_cmd import init          # noqa: E402
from bincio.serve.cli import serve              # noqa: E402
from bincio.dev import dev                      # noqa: E402
from bincio.reextract_cmd import reextract_originals  # noqa: E402

main.add_command(extract)
main.add_command(render)
main.add_command(edit)
main.add_command(import_group)
main.add_command(init)
main.add_command(serve)
main.add_command(dev)
main.add_command(reextract_originals)
