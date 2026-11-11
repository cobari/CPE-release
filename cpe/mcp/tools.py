"""Structured MCP equivalents of the 3.1 workspace, import and export actions."""

from __future__ import annotations

import copy
from functools import partial
from threading import Event
from typing import Annotated, Any, Literal

import anyio
from mcp.server import MCPServer
from mcp.types import CallToolResult, ToolAnnotations
from pydantic import Field

from cpe.mcp.response import RESPONSE_LIMIT, wire_size
from cpe.mcp.server import _result, _search
from cpe.mcp.service import SearchService
from cpe.mcp.workspace import Workspace

PageSize = Annotated[int, Field(strict=True, ge=1, le=100)]
Start = Annotated[int, Field(strict=True, ge=0)]
Sort = Literal["source", "era", "era-desc"]
BUDGET = RESPONSE_LIMIT


def _size(value: Any) -> int:
    return wire_size(value)


def bounded(payload: dict[str, Any]) -> dict[str, Any]:
    size = _size(payload)
    if size <= BUDGET:
        return payload
    return {
        "error": "response_limit",
        "bytes": size,
        "limit": BUDGET,
        **{
            key: payload[key]
            for key in ("id", "job", "search_id", "done")
            if key in payload
        },
    }


def page(payload: dict[str, Any], start: int, limit: int) -> dict[str, Any]:
    """Page collected catalog/preview metadata without fetching provider pages."""
    if "error" in payload:
        return payload
    if isinstance(payload.get("answer"), dict):
        return bounded({**payload, "answer": page(payload["answer"], start, limit)})
    key = next(
        (
            name
            for name in ("items", "databases", "books", "datasets", "sections", "units")
            if isinstance(payload.get(name), list)
        ),
        None,
    )
    if key is None:
        return bounded(payload)
    items = payload[key]
    answer = {name: value for name, value in payload.items() if name != key}
    answer.update(
        {key: [], "collected_count": len(items), "start": start, "omitted": []}
    )
    index = start
    while index < len(items) and index - start < limit:
        candidate = {
            **answer,
            key: [*answer[key], items[index]],
            "next_start": index + 1,
        }
        if _size(candidate) > BUDGET:
            if answer[key] or answer["omitted"]:
                break
            answer["omitted"].append({"index": index, "reason": "item_too_large"})
        else:
            answer[key].append(copy.deepcopy(items[index]))
        index += 1
    answer["next_start"] = index if index < len(items) else None
    return bounded(answer)


async def call(function: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
    # Mutations finish their existing atomic core operation even if the client
    # disconnects. Search/download cancellation has separate explicit checkpoints.
    return await anyio.to_thread.run_sync(partial(function, *args, **kwargs))


async def interruptible(function: Any, **kwargs: Any) -> dict[str, Any]:
    stopped = Event()

    def checkpoint() -> None:
        if stopped.is_set():
            raise InterruptedError("MCP request cancelled")

    try:
        return await anyio.to_thread.run_sync(
            partial(function, checkpoint=checkpoint, **kwargs), abandon_on_cancel=True
        )
    except BaseException:
        stopped.set()
        service = kwargs.get("service")
        if isinstance(service, SearchService):
            service.cancel_active(checkpoint)
        raise
    finally:
        stopped.set()


def register(
    server: MCPServer[Any], service: SearchService, workspace: Workspace
) -> None:
    read = ToolAnnotations(
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    )
    mutate = ToolAnnotations(
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=False,
        open_world_hint=False,
    )
    download = ToolAnnotations(
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=False,
        open_world_hint=True,
    )
    export = ToolAnnotations(
        read_only_hint=False,
        destructive_hint=True,
        idempotent_hint=False,
        open_world_hint=False,
    )

    @server.tool(annotations=read)
    async def get_workspace() -> CallToolResult:
        """Read libraries, selection, sort, normalization and host capabilities.

        Source-text characters never follow interface language. Host read/write
        roots and enabled imports are capabilities, not new permissions requested
        by a tool. CTP credentials are never returned.
        """
        return _result(bounded(await call(workspace.describe)))

    @server.tool(annotations=mutate)
    async def manage_library(
        action: Literal["list", "open", "load", "reload", "activate", "remove"],
        source_id: Literal["local", "local2", "local3", "local4"] = "local",
        path: str = "",
    ) -> CallToolResult:
        """Manage the GUI-equivalent local slots within host-selected read roots.

        open assigns a folder; reload reads changes; activate chooses the import
        destination. remove detaches a slot without deleting user files. Existing
        search IDs remain immutable snapshots of their submitted searches.
        """
        return _result(
            bounded(await call(workspace.manage_library, action, source_id, path))
        )

    @server.tool(annotations=mutate)
    async def configure_workspace(
        sources: list[str] | None = None,
        source_options: dict[str, str] | None = None,
        sort: Sort | None = None,
        language: Literal["ko", "ja", "zh-Hans", "zh-Hant", "vi", "en"] | None = None,
        normalization: dict[str, Any] | None = None,
    ) -> CallToolResult:
        """Set up to four sources, provider filters, sorting or core normalization.

        source_options uses historydb_site/historydb_item/nahf_item. Normalization
        uses existing variation_selectors/whitespace/line_breaks keep/delete,
        notes barrier/join, paired note/annotation markers and researcher-supplied
        variants {id,version,map}. Apply only researcher-selected rules; never
        infer classical variant mappings or boundaries from modern Chinese.
        Settings belong to this MCP workspace, not the GUI profile.
        """
        answer = await call(
            workspace.configure,
            sources=sources,
            source_options=source_options,
            sort=sort,
            language=language,
            normalization=normalization,
        )
        return _result(bounded(answer))

    @server.tool(annotations=download)
    async def get_source_catalog(
        source_id: str,
        site: str = "",
        start: Start = 0,
        page_size: PageSize = 20,
        catalog_id: str = "",
        refresh: bool = False,
    ) -> CallToolResult:
        """Read the GUI's DocuSky profiles, HistoryDB items or static NAHF items.

        Network catalogs require host source opt-in and use the persistent source
        client cap. start pages this returned catalog, not source search results.
        """
        return _result(
            page(
                await call(
                    workspace.cached_catalog,
                    "source",
                    source_id,
                    site,
                    catalog_id=catalog_id,
                    refresh=refresh,
                ),
                start,
                page_size,
            )
        )

    @server.tool(annotations=download)
    async def search_sources(
        source_ids: list[str] | None = None,
        mode: Literal["pair", "words"] = "pair",
        first: str = "",
        second: str = "",
        query: str = "",
        options: dict[str, str] | None = None,
        sort: Sort | None = None,
        page_size: PageSize = 20,
    ) -> CallToolResult:
        """Search up to four selected local/online sources as separate result panels.

        Omitting source_ids uses workspace selection. Each successful panel has
        its own search_id and source limits. If the combined response cannot fit,
        panel metadata remains and cursor="" retrieves its first page separately.
        A navigation-only SHIPS panel has no claimed hit count.
        """
        sources = (
            source_ids
            if source_ids is not None
            else workspace.api._selected_sources.copy()
        )
        if (
            not 1 <= len(sources) <= 4
            or len(set(sources)) != len(sources)
            or sum(
                source == "docusky" or source.startswith("docusky:")
                for source in sources
            )
            > 1
        ):
            return _result({"error": "bad_sources"})
        results = []
        for source in sources:
            answer = await _search(
                service,
                source_id=source,
                mode=mode,
                first=first,
                second=second,
                query=query,
                page_size=page_size,
                options={**workspace.api.source_options(), **(options or {})},
                sort=sort or workspace.api.saved_sort(),
            )
            results.append({"source_id": source, **answer})
        payload = {"results": results}
        if _size(payload) > BUDGET:
            payload = {
                "results": [
                    {
                        key: value
                        for key, value in result.items()
                        if key not in {"rows", "omitted", "warnings"}
                    }
                    | {
                        "first_page_omitted": "search_id" in result,
                        "retrieve_first_page_with_empty_cursor": "search_id" in result,
                    }
                    for result in results
                ]
            }
        return _result(bounded(payload))

    @server.tool(annotations=read)
    async def reorder_search(
        search_id: str, sort: Sort = "source", page_size: PageSize = 20
    ) -> CallToolResult:
        """Create a separately paged source/era-sorted snapshot without re-searching."""
        return _result(bounded(await call(service.reorder, search_id, sort, page_size)))

    @server.tool(annotations=mutate)
    async def manage_searches(
        action: Literal["list", "cancel", "clear", "clear_all"],
        search_id: str = "",
        operation_id: str = "",
    ) -> CallToolResult:
        """Inspect, stop or clear search results without deleting research files.

        cancel frees the foreground slot; a retiring worker cannot publish or
        release a newer operation's guard. At most four workers may be in flight.
        operation_id can restrict cancellation to the operation previously seen.
        clear/clear_all retire saved snapshots. Staged ITKC uses itkc_search.
        """
        return _result(
            await call(service.manage_searches, action, search_id, operation_id)
        )

    @server.tool(annotations=read)
    async def search_dsl(
        source_id: Literal["local", "local2", "local3", "local4"] = "local",
        expression: str = "",
        pattern_file: str = "",
        sort: Sort = "source",
        page_size: PageSize = 20,
    ) -> CallToolResult:
        """Search a local library using the existing full pattern DSL/TOML parser.

        Supply exactly one inline expression or a host-allowed pattern_file path.
        Patterns must come from the researcher, with research provenance; never
        invent classical examples or grammatical rules. Core evidence/positions
        are preserved in record. This is not a remote-provider DSL query.
        """
        return _result(
            await interruptible(
                workspace.search_dsl,
                service=service,
                source_id=source_id,
                expression=expression,
                pattern_file=pattern_file,
                sort=sort,
                page_size=page_size,
            )
        )

    @server.tool(annotations=download)
    async def itkc_search(
        action: Literal["start", "advance", "page", "status", "cancel", "clear"],
        mode: Literal["pair", "words"] = "pair",
        first: str = "",
        second: str = "",
        query: str = "",
        job_id: str = "",
        revision: Start = 0,
        group: Literal["likely", "less"] = "likely",
        start: Start = 0,
        sort: Sort = "source",
        page_size: PageSize = 20,
    ) -> CallToolResult:
        """Use 3.1's staged ITKC search: one collection batch per explicit advance.

        start reads the first collection. advance reads exactly the next of the
        three bounded first-page collections; it never claims an exhaustive
        provider search. page reads an immutable revision without network. Keep
        job_id/revision/group and inspect state, counts and source limits.
        status/cancel remain available while a batch is in flight.
        """
        arguments = dict(
            action=action,
            request={"mode": mode, "first": first, "second": second, "query": query},
            job_id=job_id,
            revision=revision,
            group=group,
            start=start,
            sort=sort,
            page_size=page_size,
            service=service,
        )
        answer = (
            await interruptible(workspace.itkc_search, **arguments)
            if action in {"start", "advance"}
            else await call(workspace.itkc_search, **arguments)
        )
        return _result(bounded(answer))

    @server.tool(annotations=download)
    async def get_import_catalog(
        provider: Literal["portal", "rise"],
        provider_page: Annotated[int, Field(ge=1)] = 1,
        resource_id: str = "",
        start: Start = 0,
        page_size: PageSize = 20,
        catalog_id: str = "",
        refresh: bool = False,
    ) -> CallToolResult:
        """List supported public datasets or RISE books/sections for selected import.

        RISE needs host import opt-in. provider_page explicitly fetches a provider
        page; start/page_size only page the collected response metadata.
        """
        return _result(
            page(
                await call(
                    workspace.cached_catalog,
                    "import",
                    provider,
                    provider_page=provider_page,
                    resource_id=resource_id,
                    catalog_id=catalog_id,
                    refresh=refresh,
                ),
                start,
                page_size,
            )
        )

    @server.tool(annotations=mutate)
    async def import_local_release(path: str, batch: bool = False) -> CallToolResult:
        """Import an approved release ZIP/XML or directory into the active folder.

        Requires a host write root. batch uses the existing per-file validation,
        source IDs, collision/idempotency rules, locks, reports and recovery paths.
        It does not delete source files or replace a library on conversion failure.
        """
        return _result(bounded(await call(workspace.import_local, path, batch=batch)))

    @server.tool(annotations=download)
    async def import_remote_release(
        provider: Literal["portal", "ctp"], identifier: str
    ) -> CallToolResult:
        """Download/import a listed portal release ID or a validated CTP work address.

        Requires provider import opt-in and an active host-writable folder. CTP
        credentials come only from host environment, never tool arguments. The
        existing importer keeps source/edition/evidence and provider safeguards.
        """
        return _result(
            bounded(await call(workspace.import_remote, provider, identifier))
        )

    @server.tool(annotations=download)
    async def preview_import(
        provider: Literal["gutenberg", "ndl", "rise", "cbeta", "nikh-partial"],
        address: str,
        revision: str = "",
    ) -> CallToolResult:
        """Start a cancellable preview for selected original-text import.

        Gutenberg/NDL/RISE require host opt-in and validated provider addresses.
        CBETA/NIKH-partial address is an already-downloaded host-readable XML path.
        A job ID is not a preview token; poll manage_import(progress) until done.
        Previewing never confirms original-vs-translation review or imports text.
        """
        return _result(
            bounded(await call(workspace.preview_import, provider, address, revision))
        )

    @server.tool(annotations=download)
    async def manage_import(
        action: Literal[
            "progress", "cancel", "unit", "discard", "history", "report", "commit"
        ],
        token: str = "",
        unit_id: str = "",
        job_id: str = "",
        selected: list[str] | None = None,
        edition: str = "",
        researcher_confirmed: bool = False,
        start: Start = 0,
        page_size: PageSize = 20,
        text_start: Start = 0,
        text_characters: Annotated[int, Field(strict=True, ge=1, le=20000)] = 4000,
    ) -> CallToolResult:
        """Read or cancel imports, save a report or commit reviewed units.

        Set researcher_confirmed=True only after the researcher explicitly
        confirms reviewing these selected units as originals, not translations.
        AI classification alone is not human review. edition must be provided
        from the source/researcher. commit/report require a host write root.
        unit returns a raw-text chunk with explicit code-point chunk offsets;
        it does not infer source sentence boundaries or reconstruct missing text.
        start/page_size page preview/history metadata without new downloads.
        """
        answer = await call(
            workspace.import_action,
            action,
            token=token,
            unit_id=unit_id,
            job_id=job_id,
            selected=selected,
            edition=edition,
            researcher_confirmed=researcher_confirmed,
        )
        if isinstance(answer.get("text"), str):
            text = answer["text"]
            end = min(len(text), text_start + text_characters)
            answer = {
                **answer,
                "text": text[text_start:end],
                "chunk_start": text_start,
                "next_text_start": end if end < len(text) else None,
                "total_characters": len(text),
            }
        return _result(page(answer, start, page_size))

    @server.tool(annotations=export)
    async def run_cli_export(
        mode: Literal["pair", "words", "dsl"],
        inputs: list[str],
        output_prefix: str,
        first: str = "",
        second: str = "",
        query: str = "",
        pattern_file: str = "",
        expression: str = "",
        a_min: Annotated[int, Field(ge=0)] = 1,
        a_max: Annotated[int, Field(ge=0)] = 20,
        b_min: Annotated[int, Field(ge=0)] = 1,
        b_max: Annotated[int, Field(ge=0)] = 20,
        scope: Literal["sentence", "clause"] = "sentence",
        run_id: str = "mcp",
        force: bool = False,
    ) -> CallToolResult:
        """Run the existing CLI search/export pipeline on approved input files.

        Writes canonical CSV, JSONL and .run.json using shared Spec 11 formatting,
        CSV formula guards, normalization and source provenance. Host read/write
        roots apply. Existing outputs are refused unless force=True was explicitly
        requested by the user. A DSL file and expression are mutually exclusive.
        This executes a new local search; use export_search_snapshot for an
        already-retained local/online result instead.
        """
        if pattern_file and expression:
            return _result({"error": "bad_request"})
        return _result(
            bounded(
                await call(
                    workspace.run_export,
                    mode,
                    inputs,
                    output_prefix,
                    first=first,
                    second=second,
                    query=query,
                    pattern_file=pattern_file,
                    expression=expression,
                    a_min=a_min,
                    a_max=a_max,
                    b_min=b_min,
                    b_max=b_max,
                    scope=scope,
                    run_id=run_id,
                    force=force,
                )
            )
        )

    @server.tool(annotations=export)
    async def export_search_snapshot(
        search_id: str, output_prefix: str, force: bool = False
    ) -> CallToolResult:
        """Export collected snapshot rows without repeating provider requests.

        Uses snapshot CSV/JSONL schema plus metadata, preserving reference-only
        rows as references (no invented cpe.result grade/positions). This differs
        from canonical CLI export. Writes require a host write root; force may
        replace existing outputs only on explicit user instruction.
        """
        return _result(
            bounded(
                await call(
                    workspace.export_snapshot,
                    service,
                    search_id,
                    output_prefix,
                    force=force,
                )
            )
        )
