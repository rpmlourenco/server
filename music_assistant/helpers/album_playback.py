"""Keep an explicitly selected local album edition on its queue items."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

from music_assistant_models.errors import MediaNotFoundError
from music_assistant_models.media_items import Album, Track

if TYPE_CHECKING:
    from collections.abc import Iterable

    from music_assistant_models.media_items import ProviderMapping
    from music_assistant_models.queue_item import QueueItem
    from music_assistant_models.streamdetails import StreamDetails

    from music_assistant.mass import MusicAssistant
    from music_assistant.providers.filesystem_local import LocalFileSystemProvider

ATTR_LOCAL_ALBUM_MAPPINGS = "personal_local_album_mappings"


async def local_album_ids(mass: MusicAssistant, album: Album) -> set[tuple[str, str]]:
    """Resolve the exact local provider identities of an explicitly selected album."""
    identities: set[tuple[str, str]] = set()
    for mapping in album.provider_mappings:
        if mapping.provider_domain != "filesystem_local":
            continue
        provider = cast(
            "LocalFileSystemProvider | None", mass.get_provider(mapping.provider_instance)
        )
        if provider is None or provider.instance_id != mapping.provider_instance:
            raise MediaNotFoundError(f"Local album source unavailable: {album.name}")
        if await provider.exists(mapping.item_id):
            identities.add((mapping.provider_instance, mapping.item_id))
            continue
        # Synthetic ids need resolving, but a refresh must not substitute another
        # edition when a shared recording points at several album folders.
        resolved = await provider.get_album(mapping.item_id)
        if resolved.version != album.version:
            raise MediaNotFoundError(f"Local album edition unavailable: {album.name}")
        identities.add((mapping.provider_instance, resolved.item_id))
    return identities


async def pin_local_album(
    mass: MusicAssistant, queue_item: QueueItem, identities: set[tuple[str, str]]
) -> None:
    """Restrict one queue occurrence to the selected local album without changing its track."""
    if not identities or not isinstance(queue_item.media_item, Track):
        return
    selected: list[list[str]] = []
    for mapping in queue_item.media_item.provider_mappings:
        if not any(instance == mapping.provider_instance for instance, _ in identities):
            continue
        provider = cast(
            "LocalFileSystemProvider | None", mass.get_provider(mapping.provider_instance)
        )
        if provider is None or provider.instance_id != mapping.provider_instance:
            continue
        try:
            track = await provider.get_track(mapping.item_id)
        except MediaNotFoundError:
            continue
        if (
            isinstance(track.album, Album)
            and (mapping.provider_instance, track.album.item_id) in identities
        ):
            selected.append([mapping.provider_instance, mapping.item_id])
    # Keep even an empty restriction: failure must never substitute another edition.
    queue_item.extra_attributes[ATTR_LOCAL_ALBUM_MAPPINGS] = json.dumps(selected)


def album_playback_mappings(
    queue_item: QueueItem, mappings: Iterable[ProviderMapping]
) -> list[ProviderMapping]:
    """Return the mappings permitted for this queue occurrence."""
    restriction = queue_item.extra_attributes.get(ATTR_LOCAL_ALBUM_MAPPINGS)
    if restriction is None:
        return list(mappings)
    if not isinstance(restriction, str):
        raise MediaNotFoundError("Invalid local album playback restriction")
    selected = json.loads(restriction)
    return [
        mapping for mapping in mappings if [mapping.provider_instance, mapping.item_id] in selected
    ]


def album_stream_matches(queue_item: QueueItem, details: StreamDetails) -> bool:
    """Return whether cached stream details belong to the selected local edition."""
    restriction = queue_item.extra_attributes.get(ATTR_LOCAL_ALBUM_MAPPINGS)
    if restriction is None:
        return True
    return isinstance(restriction, str) and [details.provider, details.item_id] in json.loads(
        restriction
    )
