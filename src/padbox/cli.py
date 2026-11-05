"""PADBox command-line entry point."""

import json
import logging
from pathlib import Path
from typing import Annotated

import typer

from padbox.database.store import Store
from padbox.sources.adapters import CuratedSource

app = typer.Typer(no_args_is_help=True, help="PADBox — local PAD collection indexing and review")
sources = typer.Typer(no_args_is_help=True, help="Load local or pinned public reference snapshots")
app.add_typer(sources, name="sources")


@app.callback()
def main(
    ctx: typer.Context,
    home: Annotated[Path, typer.Option(envvar="PADBOX_HOME", help="Local data directory")] = Path(
        ".padbox"
    ),
) -> None:
    store = Store(home)
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(store.home / "padbox.log")],
        force=True,
    )
    ctx.obj = store


def emit(value: object) -> None:
    typer.echo(json.dumps(value, indent=2, default=str))


@sources.command("load")
def load_source(ctx: typer.Context, manifest: Path) -> None:
    """Load a curated JSON list with local icons (see docs/reference-manifest.md)."""
    emit({"loaded": ctx.obj.load_monsters(CuratedSource(manifest).load())})


if __name__ == "__main__":
    app()
