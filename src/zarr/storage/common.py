from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from zarr.abc.store import ByteRangeRequest, Store
from zarr.core.buffer import Buffer, default_buffer_prototype
from zarr.core.common import ZARR_JSON, ZARRAY_JSON, ZGROUP_JSON, ZarrFormat
from zarr.errors import ContainsArrayAndGroupError, ContainsArrayError, ContainsGroupError
from zarr.storage._utils import normalize_path
from zarr.storage.local import LocalStore
from zarr.storage.memory import MemoryStore

# from zarr.store.remote import RemoteStore

if TYPE_CHECKING:
    from zarr.core.buffer import BufferPrototype
    from zarr.core.common import AccessModeLiteral


def _dereference_path(root: str, path: str) -> str:
    assert isinstance(root, str)
    assert isinstance(path, str)
    root = root.rstrip("/")
    path = f"{root}/{path}" if root else path
    return path.rstrip("/")


class StorePath:
    """
    Path-like interface for a Store.

    Parameters
    ----------
    store : Store
        The store to use.
    path : str
        The path within the store.
    """

    store: Store
    path: str

    def __init__(self, store: Store, path: str = "") -> None:
        self.store = store
        self.path = path

    async def get(
        self,
        prototype: BufferPrototype | None = None,
        byte_range: ByteRangeRequest | None = None,
    ) -> Buffer | None:
        """
        Read bytes from the store.

        Parameters
        ----------
        prototype : BufferPrototype, optional
            The buffer prototype to use when reading the bytes.
        byte_range : ByteRangeRequest, optional
            The range of bytes to read.

        Returns
        -------
        buffer : Buffer or None
            The read bytes, or None if the key does not exist.
        """
        if prototype is None:
            prototype = default_buffer_prototype()
        return await self.store.get(self.path, prototype=prototype, byte_range=byte_range)

    async def set(self, value: Buffer, byte_range: ByteRangeRequest | None = None) -> None:
        """
        Write bytes to the store.

        Parameters
        ----------
        value : Buffer
            The buffer to write.
        byte_range : ByteRangeRequest, optional
            The range of bytes to write. If None, the entire buffer is written.

        Raises
        ------
        NotImplementedError
            If `byte_range` is not None, because Store.set does not support partial writes yet.
        """
        if byte_range is not None:
            raise NotImplementedError("Store.set does not have partial writes yet")
        await self.store.set(self.path, value)

    async def delete(self) -> None:
        """
        Delete the key from the store.

        Raises
        ------
        NotImplementedError
            If the store does not support deletion.
        """
        await self.store.delete(self.path)

    async def set_if_not_exists(self, default: Buffer) -> None:
        """
        Store a key to ``value`` if the key is not already present.

        Parameters
        ----------
        default : Buffer
            The buffer to store if the key is not already present.
        """
        await self.store.set_if_not_exists(self.path, default)

    async def exists(self) -> bool:
        """
        Check if the key exists in the store.

        Returns
        -------
        bool
            True if the key exists in the store, False otherwise.
        """
        return await self.store.exists(self.path)

    def __truediv__(self, other: str) -> StorePath:
        """Combine this store path with another path"""
        return self.__class__(self.store, _dereference_path(self.path, other))

    def __str__(self) -> str:
        return _dereference_path(str(self.store), self.path)

    def __repr__(self) -> str:
        return f"StorePath({self.store.__class__.__name__}, {str(self)!r})"

    def __eq__(self, other: object) -> bool:
        """
        Check if two StorePath objects are equal.

        Returns
        -------
        bool
            True if the two objects are equal, False otherwise.

        Notes
        -----
        Two StorePath objects are considered equal if their stores are equal
        and their paths are equal.
        """
        try:
            return self.store == other.store and self.path == other.path  # type: ignore[attr-defined, no-any-return]
        except Exception:
            pass
        return False


StoreLike = Store | StorePath | Path | str | dict[str, Buffer]


async def make_store_path(
    store_like: StoreLike | None,
    *,
    path: str | None = "",
    mode: AccessModeLiteral | None = None,
    storage_options: dict[str, Any] | None = None,
) -> StorePath:
    """
    Convert a `StoreLike` object into a StorePath object.

    This function takes a `StoreLike` object and returns a `StorePath` object.  The
    `StoreLike` object can be a `Store`, `StorePath`, `Path`, `str`, or `dict[str, Buffer]`.
    If the `StoreLike` object is a Store or `StorePath`, it is converted to a
    `StorePath` object.  If the `StoreLike` object is a Path or str, it is converted
    to a LocalStore object and then to a `StorePath` object.  If the `StoreLike`
    object is a dict[str, Buffer], it is converted to a `MemoryStore` object and
    then to a `StorePath` object.

    If the `StoreLike` object is None, a `MemoryStore` object is created and
    converted to a `StorePath` object.

    If the `StoreLike` object is a str and starts with a protocol, it is
    converted to a RemoteStore object and then to a `StorePath` object.

    If the `StoreLike` object is a dict[str, Buffer] and the mode is not None,
    the `MemoryStore` object is created with the given mode.

    If the `StoreLike` object is a str and starts with a protocol, the
    RemoteStore object is created with the given mode and storage options.

    Parameters
    ----------
    store_like : StoreLike | None
        The object to convert to a `StorePath` object.
    path : str | None, optional
        The path to use when creating the `StorePath` object.  If None, the
        default path is the empty string.
    mode : AccessModeLiteral | None, optional
        The mode to use when creating the `StorePath` object.  If None, the
        default mode is 'r'.
    storage_options : dict[str, Any] | None, optional
        The storage options to use when creating the `RemoteStore` object.  If
        None, the default storage options are used.

    Returns
    -------
    StorePath
        The converted StorePath object.

    Raises
    ------
    TypeError
        If the StoreLike object is not one of the supported types.
    """
    from zarr.storage.remote import RemoteStore  # circular import

    used_storage_options = False
    path_normalized = normalize_path(path)
    if isinstance(store_like, StorePath):
        if mode is not None and mode != store_like.store.mode.str:
            _store = store_like.store.with_mode(mode)
            await _store._ensure_open()
            store_like = StorePath(_store, path=store_like.path)
        result = store_like / path_normalized
    elif isinstance(store_like, Store):
        if mode is not None and mode != store_like.mode.str:
            store_like = store_like.with_mode(mode)
        await store_like._ensure_open()
        result = StorePath(store_like, path=path_normalized)
    elif store_like is None:
        # mode = "w" is an exception to the default mode = 'r'
        result = StorePath(await MemoryStore.open(mode=mode or "w"), path=path_normalized)
    elif isinstance(store_like, Path):
        result = StorePath(
            await LocalStore.open(root=store_like, mode=mode or "r"), path=path_normalized
        )
    elif isinstance(store_like, str):
        storage_options = storage_options or {}

        if _is_fsspec_uri(store_like):
            used_storage_options = True
            result = StorePath(
                RemoteStore.from_url(store_like, storage_options=storage_options, mode=mode or "r"),
                path=path_normalized,
            )
        else:
            result = StorePath(
                await LocalStore.open(root=Path(store_like), mode=mode or "r"), path=path_normalized
            )
    elif isinstance(store_like, dict):
        # We deliberate only consider dict[str, Buffer] here, and not arbitrary mutable mappings.
        # By only allowing dictionaries, which are in-memory, we know that MemoryStore appropriate.
        result = StorePath(
            await MemoryStore.open(store_dict=store_like, mode=mode or "r"), path=path_normalized
        )
    else:
        msg = f"Unsupported type for store_like: '{type(store_like).__name__}'"  # type: ignore[unreachable]
        raise TypeError(msg)

    if storage_options and not used_storage_options:
        msg = "'storage_options' was provided but unused. 'storage_options' is only used for fsspec filesystem stores."
        raise TypeError(msg)

    return result


def _is_fsspec_uri(uri: str) -> bool:
    """
    Check if a URI looks like a non-local fsspec URI.

    Examples
    --------
    >>> _is_fsspec_uri("s3://bucket")
    True
    >>> _is_fsspec_uri("my-directory")
    False
    >>> _is_fsspec_uri("local://my-directory")
    False
    """
    return "://" in uri or ("::" in uri and "local://" not in uri)


async def ensure_no_existing_node(store_path: StorePath, zarr_format: ZarrFormat) -> None:
    """
    Check if a store_path is safe for array / group creation.
    Returns `None` or raises an exception.

    Parameters
    ----------
    store_path : StorePath
        The storage location to check.
    zarr_format : ZarrFormat
        The Zarr format to check.

    Raises
    ------
    ContainsArrayError, ContainsGroupError, ContainsArrayAndGroupError
    """
    if zarr_format == 2:
        extant_node = await _contains_node_v2(store_path)
    elif zarr_format == 3:
        extant_node = await _contains_node_v3(store_path)

    if extant_node == "array":
        raise ContainsArrayError(store_path.store, store_path.path)
    elif extant_node == "group":
        raise ContainsGroupError(store_path.store, store_path.path)
    elif extant_node == "nothing":
        return
    msg = f"Invalid value for extant_node: {extant_node}"  # type: ignore[unreachable]
    raise ValueError(msg)


async def _contains_node_v3(store_path: StorePath) -> Literal["array", "group", "nothing"]:
    """
    Check if a store_path contains nothing, an array, or a group. This function
    returns the string "array", "group", or "nothing" to denote containing an array, a group, or
    nothing.

    Parameters
    ----------
    store_path : StorePath
        The location in storage to check.

    Returns
    -------
    Literal["array", "group", "nothing"]
        A string representing the zarr node found at store_path.
    """
    result: Literal["array", "group", "nothing"] = "nothing"
    extant_meta_bytes = await (store_path / ZARR_JSON).get()
    # if no metadata document could be loaded, then we just return "nothing"
    if extant_meta_bytes is not None:
        try:
            extant_meta_json = json.loads(extant_meta_bytes.to_bytes())
            # avoid constructing a full metadata document here in the name of speed.
            if extant_meta_json["node_type"] == "array":
                result = "array"
            elif extant_meta_json["node_type"] == "group":
                result = "group"
        except (KeyError, json.JSONDecodeError):
            # either of these errors is consistent with no array or group present.
            pass
    return result


async def _contains_node_v2(store_path: StorePath) -> Literal["array", "group", "nothing"]:
    """
    Check if a store_path contains nothing, an array, a group, or both. If both an array and a
    group are detected, a `ContainsArrayAndGroup` exception is raised. Otherwise, this function
    returns the string "array", "group", or "nothing" to denote containing an array, a group, or
    nothing.

    Parameters
    ----------
    store_path : StorePath
        The location in storage to check.

    Returns
    -------
    Literal["array", "group", "nothing"]
        A string representing the zarr node found at store_path.
    """
    _array = await contains_array(store_path=store_path, zarr_format=2)
    _group = await contains_group(store_path=store_path, zarr_format=2)

    if _array and _group:
        raise ContainsArrayAndGroupError(store_path.store, store_path.path)
    elif _array:
        return "array"
    elif _group:
        return "group"
    else:
        return "nothing"


async def contains_array(store_path: StorePath, zarr_format: ZarrFormat) -> bool:
    """
    Check if an array exists at a given StorePath.

    Parameters
    ----------
    store_path : StorePath
        The StorePath to check for an existing group.
    zarr_format :
        The zarr format to check for.

    Returns
    -------
    bool
        True if the StorePath contains a group, False otherwise.

    """
    if zarr_format == 3:
        extant_meta_bytes = await (store_path / ZARR_JSON).get()
        if extant_meta_bytes is None:
            return False
        else:
            try:
                extant_meta_json = json.loads(extant_meta_bytes.to_bytes())
                # we avoid constructing a full metadata document here in the name of speed.
                if extant_meta_json["node_type"] == "array":
                    return True
            except (ValueError, KeyError):
                return False
    elif zarr_format == 2:
        return await (store_path / ZARRAY_JSON).exists()
    msg = f"Invalid zarr_format provided. Got {zarr_format}, expected 2 or 3"
    raise ValueError(msg)


async def contains_group(store_path: StorePath, zarr_format: ZarrFormat) -> bool:
    """
    Check if a group exists at a given StorePath.

    Parameters
    ----------

    store_path : StorePath
        The StorePath to check for an existing group.
    zarr_format :
        The zarr format to check for.

    Returns
    -------

    bool
        True if the StorePath contains a group, False otherwise

    """
    if zarr_format == 3:
        extant_meta_bytes = await (store_path / ZARR_JSON).get()
        if extant_meta_bytes is None:
            return False
        else:
            try:
                extant_meta_json = json.loads(extant_meta_bytes.to_bytes())
                # we avoid constructing a full metadata document here in the name of speed.
                result: bool = extant_meta_json["node_type"] == "group"
            except (ValueError, KeyError):
                return False
            else:
                return result
    elif zarr_format == 2:
        return await (store_path / ZGROUP_JSON).exists()
    msg = f"Invalid zarr_format provided. Got {zarr_format}, expected 2 or 3"  # type: ignore[unreachable]
    raise ValueError(msg)
