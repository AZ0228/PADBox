"""PADBox command-line entry point."""

import json
import logging
from pathlib import Path
from typing import Annotated

import typer

from padbox.database.store import Store
from padbox.sources.adapters import PINNED_COMMIT, CuratedSource, FormationSource

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


@sources.command("sync")
def sync_source(
    ctx: typer.Context,
    ids: Annotated[
        str, typer.Option(help="Comma-separated icon IDs; metadata loads all")
    ] = "13966,13967,13968",
    all_icons: Annotated[
        bool, typer.Option(help="Cache all NA icons (large first download)")
    ] = False,
    commit: Annotated[str, typer.Option(help="Full public snapshot commit SHA")] = PINNED_COMMIT,
) -> None:
    """Download a pinned PADDashFormation NA snapshot and selected icon sheets."""
    try:
        selected = {int(value.strip()) for value in ids.split(",") if value.strip()}
        store = ctx.obj
        source = FormationSource(
            store.home / "cache", store.home / "icons", selected, commit, all_icons
        )
        records = source.load()
        report = store.home / "source-report.json"
        report.write_text(json.dumps(source.report, indent=2))
        emit(
            {
                "loaded": store.load_monsters(records),
                "icons_requested": "all NA" if all_icons else sorted(selected),
                "icons_available": sum(m.icon is not None for m in records),
                "source_report": str(report),
                "note": "Only cached icons can be matched. See docs/data-sources.md.",
            }
        )
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc


if __name__ == "__main__":
    app()
