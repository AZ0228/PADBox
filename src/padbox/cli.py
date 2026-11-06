"""PADBox command-line entry point."""

import json
import logging
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import cv2
import typer

from padbox.database.store import Store
from padbox.detection.grid import annotate, detect
from padbox.export.collection import collection, csv_export
from padbox.inventory.pipeline import import_media, rematch
from padbox.inventory.service import accept, add, set_entry_quantity, set_quantity, statistics
from padbox.inventory.snapshots import apply_scan, history, preview_scan, scan_list
from padbox.models import Layout
from padbox.recognition.evaluate import evaluate
from padbox.recognition.matcher import Matcher
from padbox.sources.adapters import PINNED_COMMIT, CuratedSource, FormationSource
from padbox.video.reader import frames, metadata

app = typer.Typer(no_args_is_help=True, help="PADBox — local PAD collection indexing and review")
sources = typer.Typer(no_args_is_help=True, help="Load local or pinned public reference snapshots")
app.add_typer(sources, name="sources")
scans = typer.Typer(no_args_is_help=True, help="Full-box drafts, diffs and replacement history")
app.add_typer(scans, name="scans")
team = typer.Typer(
    no_args_is_help=True, help="Score a named team, search the box, or compare two teams"
)
app.add_typer(team, name="team")


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


@app.command("import")
def import_command(
    ctx: typer.Context,
    recording: Path,
    layout: Annotated[Path | None, typer.Option(help="Normalized layout JSON")] = None,
    interval: Annotated[float, typer.Option(min=0.01, help="Sample interval in seconds")] = 0.3,
) -> None:
    """Capture a replacement draft; strong matches are accepted within the draft."""
    try:
        settings = Layout.model_validate_json(layout.read_text()) if layout else Layout()
        emit(import_media(ctx.obj, recording, settings, interval))
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command()
def inspect(
    recording: Path, output: Path = Path(".padbox/inspection.png"), layout: Path | None = None
) -> None:
    """Save an annotated first-frame preview for detection/calibration."""
    settings = Layout.model_validate_json(layout.read_text()) if layout else Layout()
    frame = next(frames(recording))
    cells = detect(frame.image, settings)
    output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output), annotate(frame.image, cells)):
        raise typer.BadParameter("Failed writing inspection image")
    emit({"metadata": metadata(recording), "cells": len(cells), "preview": output.resolve()})


@app.command()
def review(ctx: typer.Context, port: int = 8501) -> None:
    """Launch local review UI, bound to localhost."""
    script = Path(__file__).parent / "review" / "app.py"
    environment = {**os.environ, "PADBOX_HOME": str(ctx.obj.home)}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(script),
            "--server.address",
            "127.0.0.1",
            "--server.port",
            str(port),
            "--server.headless",
            "true",
            "--browser.gatherUsageStats",
            "false",
        ],
        env=environment,
        check=False,
    )
    raise typer.Exit(result.returncode)


@app.command()
def live(
    ctx: typer.Context,
    port: Annotated[int, typer.Option(min=1, max=65535)] = 8502,
    layout: Annotated[Path | None, typer.Option(help="Default live phone layout JSON")] = None,
    background: Annotated[
        bool, typer.Option(help="Keep the local server running after this command")
    ] = False,
    stop: Annotated[
        bool, typer.Option(help="Stop this collection's live server on the chosen port")
    ] = False,
) -> None:
    """Launch local live window capture and immediate review in your browser."""
    from padbox.live.lifecycle import start_background, stop_background
    from padbox.live.server import LiveServer

    if stop and background:
        raise typer.BadParameter("Choose either --background or --stop")
    home = ctx.obj.home
    try:
        if stop:
            stopped = stop_background(home, port)
            typer.echo(
                "Live server stopped; collection saved."
                if stopped
                else "Live server is not running."
            )
            return
        saved_layout = home / "live-layout.json"
        layout_path = layout or (saved_layout if saved_layout.exists() else None)
        settings = Layout.model_validate_json(layout_path.read_text()) if layout_path else Layout()
        if background:
            ready = start_background(home, port, layout_path)
            typer.echo(f"PADBox live scan: http://127.0.0.1:{port}")
            typer.echo(
                f"Background server ready (PID {ready['pid']}). "
                "It stays running after this command."
            )
            typer.echo(f"Log: {home / f'live-server-{port}.log'}")
            return
        server = LiveServer(ctx.obj, port, settings)
        saved_layout.write_text(settings.model_dump_json(indent=2))
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(f"Cannot start/manage live server on port {port}: {exc}") from exc
    typer.echo(f"PADBox live scan: {server.origin}")
    typer.echo("Keep this terminal open. Press Ctrl+C to stop; saved entries are retained.")
    typer.echo("Open this URL in Chrome/Edge. Select your QuickTime iPhone preview window.")
    pidfile = home / f"live-server-{port}.pid"
    pidfile.write_text(str(os.getpid()))

    def terminate(_signal: int, _frame: object) -> None:
        raise KeyboardInterrupt

    previous_handler = signal.signal(signal.SIGTERM, terminate)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        typer.echo("Live sessions saved and paused.")
    finally:
        server.server_close()
        signal.signal(signal.SIGTERM, previous_handler)
        if pidfile.exists() and pidfile.read_text() == str(os.getpid()):
            pidfile.unlink()


@app.command("export")
def export_command(
    ctx: typer.Context,
    format: Annotated[str, typer.Option(help="json or csv")] = "json",
    output: Annotated[Path | None, typer.Option()] = None,
    confirmed_only: bool = False,
) -> None:
    """Export accepted quantities and separate unresolved proposals."""
    if format == "json":
        content = json.dumps(collection(ctx.obj, confirmed_only), indent=2) + "\n"
    elif format == "csv":
        content = csv_export(ctx.obj, confirmed_only)
    else:
        raise typer.BadParameter("Format must be json or csv")
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(content)
    else:
        typer.echo(content, nl=False)


@app.command()
def stats(ctx: typer.Context, scan: str | None = None) -> None:
    """Show current applied box statistics, or a specific draft with --scan."""
    emit(statistics(ctx.obj, scan))


@app.command("add")
def add_command(
    ctx: typer.Context,
    monster_id: int,
    quantity: int = 1,
    scan: Annotated[str | None, typer.Option(help="Replacement draft ID")] = None,
) -> None:
    """Manually add missed physical copies to a draft by reference ID."""
    try:
        emit({"inventory_ids": add(ctx.obj, monster_id, quantity, scan)})
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command("quantity")
def quantity_command(ctx: typer.Context, monster_id: int, quantity: int) -> None:
    """Set confirmed owned count; unresolved evidence is retained."""
    try:
        set_quantity(ctx.obj, monster_id, quantity)
        emit(statistics(ctx.obj))
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command("accept")
def accept_command(
    ctx: typer.Context, inventory_id: str, monster_id: int, distinct: bool = False
) -> None:
    """Confirm/correct an entry; --distinct explicitly resolves slot ambiguity."""
    try:
        accept(ctx.obj, inventory_id, monster_id, distinct)
        emit(statistics(ctx.obj))
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command("rematch")
def rematch_command(ctx: typer.Context) -> None:
    emit({"updated_unreviewed_entries": rematch(ctx.obj)})


@app.command("entry-quantity")
def entry_quantity(ctx: typer.Context, inventory_id: str, quantity: int) -> None:
    """Verify the displayed xN quantity of a draft slot."""
    try:
        set_entry_quantity(ctx.obj, inventory_id, quantity)
        emit({"inventory_id": inventory_id, "quantity": quantity})
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


@scans.command("list")
def list_scans(ctx: typer.Context) -> None:
    emit(scan_list(ctx.obj))


@scans.command("diff")
def diff_scan(ctx: typer.Context, scan_id: str, output: Path | None = None) -> None:
    """Preview additions/removals against the currently applied box."""
    try:
        preview = preview_scan(ctx.obj, scan_id)
        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(preview, indent=2) + "\n")
        emit(preview)
    except (ValueError, OSError) as exc:
        raise typer.BadParameter(str(exc)) from exc


@scans.command("apply")
def apply_snapshot(
    ctx: typer.Context,
    scan_id: str,
    preview: Annotated[Path, typer.Option(help="Reviewed JSON from scans diff --output")],
    full_box: Annotated[bool, typer.Option(help="I verified the complete unfiltered box")] = False,
    expected_slots: Annotated[int | None, typer.Option(min=1)] = None,
) -> None:
    """Replace the entire current box with the exact reviewed draft."""
    try:
        document = json.loads(preview.read_text())
        if document["scan_id"] != scan_id:
            raise ValueError("Preview belongs to a different scan")
        emit(apply_scan(ctx.obj, scan_id, document["preview_token"], full_box, expected_slots))
    except (ValueError, OSError, KeyError) as exc:
        raise typer.BadParameter(str(exc)) from exc


@scans.command("history")
def scan_history(ctx: typer.Context) -> None:
    emit(history(ctx.obj))


@app.command()
def explain(ctx: typer.Context, monster_id: int) -> None:
    """Show verified skill effects for a monster and its transform/assist forms."""
    from padbox.mechanics.explain import explain_monster

    try:
        emit(explain_monster(ctx.obj, monster_id))
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command()
def cover(
    ctx: typer.Context,
    leader: int,
    helper: Annotated[
        int | None, typer.Option(help="Borrowed helper; it does not need to be owned")
    ] = None,
    output: Annotated[Path | None, typer.Option(help="Write the report JSON")] = None,
) -> None:
    """Report coverage holes for one leader against the applied box."""
    from padbox.mechanics.coverage import cover_box

    try:
        report = cover_box(ctx.obj, leader, helper)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + "\n")
    emit(report)


@app.command()
def pool(
    ctx: typer.Context,
    leader: int,
    helper: Annotated[
        int | None, typer.Option(help="Borrowed helper; it does not need to be owned")
    ] = None,
    output: Annotated[Path | None, typer.Option(help="Write the pool JSON")] = None,
) -> None:
    """List owned and reachable forms that might fill this leader's holes."""
    from padbox.mechanics.team import pool_box

    try:
        report = pool_box(ctx.obj, leader, helper)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + "\n")
    emit(report)


@app.command()
def shortlist(
    ctx: typer.Context,
    leader: int,
    helper: Annotated[
        int | None, typer.Option(help="Borrowed helper; it does not need to be owned")
    ] = None,
    output: Annotated[Path | None, typer.Option(help="Write the shortlist JSON")] = None,
) -> None:
    """List the best subs for each requirement, with assist options."""
    from padbox.mechanics.team import shortlist_box

    try:
        report = shortlist_box(ctx.obj, leader, helper)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + "\n")
    emit(report)


@app.command("check-team")
def check_team_command(
    ctx: typer.Context,
    leader: int,
    team: Annotated[Path, typer.Argument(help="JSON file naming subs and assists")],
) -> None:
    """Check a named team's shape, ownership, evolution, transforms, coverage, and unknowns."""
    from padbox.mechanics.team import check_team

    try:
        payload = json.loads(team.read_text())
        if not isinstance(payload, dict):
            raise ValueError("Team file must be an object")
        report = check_team(ctx.obj, leader, payload)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    emit(report)


@team.command("evaluate")
def team_evaluate(
    ctx: typer.Context,
    team_file: Annotated[
        Path, typer.Option("--team", help="JSON team with leader_id, subs, and assists")
    ],
    objective: Annotated[str, typer.Option(help="Scoring objective")] = "general",
    seed: Annotated[int, typer.Option(help="Monte Carlo seed")] = 0,
    turns: Annotated[int, typer.Option(min=1)] = 10,
    simulations: Annotated[int, typer.Option(min=1)] = 1000,
    output: Annotated[Path | None, typer.Option(help="Write the report JSON")] = None,
) -> None:
    """Score one supplied team. This does not search."""
    from pydantic import ValidationError

    from padbox.evaluator import Scenario, evaluate_team

    try:
        payload = json.loads(team_file.read_text())
        if not isinstance(payload, dict):
            raise ValueError("Team file must be an object")
        scenario = Scenario(
            name=objective,  # type: ignore[arg-type]
            seed=seed,
            turns=turns,
            simulations=simulations,
        )
        report = evaluate_team(payload, scenario, store=ctx.obj)
    except (ValueError, OSError, json.JSONDecodeError, ValidationError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    _write_json(output, report.model_dump(mode="json"))


@team.command("optimize")
def team_optimize(
    ctx: typer.Context,
    leader: Annotated[int, typer.Option("--leader", help="Leader monster id")],
    helper: Annotated[int | None, typer.Option("--helper", help="Explicit helper id")] = None,
    dungeon: Annotated[
        Path | None,
        typer.Option("--dungeon", help="Not applied. A dungeon file does not change this search"),
    ] = None,
    objective: Annotated[str, typer.Option(help="Scoring objective")] = "general",
    beam_width: Annotated[int, typer.Option(min=1)] = 200,
    seed: Annotated[int, typer.Option()] = 0,
    turns: Annotated[int, typer.Option(min=1)] = 10,
    simulations: Annotated[int, typer.Option(min=1)] = 1000,
    output: Annotated[Path | None, typer.Option(help="Write the ranked JSON")] = None,
) -> None:
    """Search the owned box. Invoke as team optimize."""
    from pydantic import ValidationError

    from padbox.optimizer import search_teams

    if dungeon is not None:
        raise typer.BadParameter(
            f"{dungeon} was not applied. team optimize does not read a dungeon profile, "
            "and this search cannot claim a dungeon clear."
        )
    try:
        result = search_teams(
            ctx.obj,
            leader,
            helper,
            objective=objective,  # type: ignore[arg-type]
            beam_width=beam_width,
            seed=seed,
            turns=turns,
            simulations=simulations,
        )
    except (ValueError, ValidationError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    _write_json(output, _search_payload(result))


@team.command("compare")
def team_compare(
    ctx: typer.Context,
    left: Annotated[Path, typer.Argument(help="First team JSON")],
    right: Annotated[Path, typer.Argument(help="Second team JSON")],
    objective: Annotated[str, typer.Option(help="Scoring objective")] = "general",
    seed: Annotated[int, typer.Option()] = 0,
    turns: Annotated[int, typer.Option(min=1)] = 10,
    simulations: Annotated[int, typer.Option(min=1)] = 1000,
) -> None:
    """Compare two named teams. Neither team is selected."""
    from pydantic import ValidationError

    from padbox.evaluator import Scenario
    from padbox.evaluator.explain import compare_teams

    try:
        first = json.loads(left.read_text())
        second = json.loads(right.read_text())
        if not isinstance(first, dict) or not isinstance(second, dict):
            raise ValueError("Each team file must be an object")
        scenario = Scenario(
            name=objective,  # type: ignore[arg-type]
            seed=seed,
            turns=turns,
            simulations=simulations,
        )
        report = compare_teams(
            first,
            second,
            scenario,
            store=ctx.obj,
            left_label=left.name,
            right_label=right.name,
        )
    except (ValueError, OSError, json.JSONDecodeError, ValidationError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    emit(report.model_dump(mode="json"))


def _search_payload(result: object) -> dict:
    """JSON for one search. Ranked reports are models, so dump them explicitly."""
    payload = result.model_dump(mode="json")  # type: ignore[attr-defined]
    for bucket in ("beam", "ranked"):
        for row, team in zip(payload[bucket], getattr(result, bucket), strict=True):
            report = team.report
            row["report"] = None if report is None else report.model_dump(mode="json")
    return payload


def _write_json(output: Path | None, payload: object) -> None:
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2) + "\n")
    emit(payload)


@app.command("evaluate")
def evaluate_command(ctx: typer.Context, labels: Path, output: Path | None = None) -> None:
    report = evaluate(Matcher(ctx.obj.references()), labels)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + "\n")
    emit(report)


@app.command("demo")
def demo_command(ctx: typer.Context) -> None:
    """Generate original synthetic fixtures, import video and evaluate; use a separate --home."""
    from padbox.demo import Demo

    if ctx.obj.references() or statistics(ctx.obj)["active_entries"]:
        raise typer.BadParameter("Demo requires an empty --home so synthetic cards stay isolated")
    paths = Demo(ctx.obj.home / "fixtures").generate()
    ctx.obj.load_monsters(CuratedSource(paths["references"]).load())
    result = import_media(ctx.obj, paths["video"])
    report = evaluate(Matcher(ctx.obj.references()), paths["labels"])
    (ctx.obj.home / "evaluation.json").write_text(json.dumps(report, indent=2))
    emit({"fixtures": paths, "import": result, "evaluation": report})


if __name__ == "__main__":
    app()
