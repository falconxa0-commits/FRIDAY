import logging
from typing import Any, Dict, List, Optional

from config.settings import SUPABASE_URL, SUPABASE_KEY

logger = logging.getLogger(__name__)


class SupabaseClient:
    """Client wrapper for Supabase with graceful degradation.

    If SUPABASE_URL or SUPABASE_KEY is not configured, the client will be
    set to None and all methods will return empty results instead of crashing.
    """

    def __init__(self) -> None:
        self.url: Optional[str] = SUPABASE_URL
        self.key: Optional[str] = SUPABASE_KEY
        self.client: Any = None

        if self.url and self.key:
            try:
                from supabase import create_client, Client
                self.client: Client = create_client(self.url, self.key)
                logger.info("Supabase client initialized successfully.")
            except Exception as exc:
                logger.warning(f"Failed to initialize Supabase client: {exc}")
                self.client = None
        else:
            logger.warning(
                "Supabase URL or Key not configured. "
                "All database operations will return empty results. "
                "Set SUPABASE_URL and SUPABASE_KEY in your .env to enable persistence."
            )

    def get_client(self) -> Any:
        """Return the underlying Supabase client (may be None)."""
        return self.client

    # ------------------------------------------------------------------
    # CRUD helpers
    # ------------------------------------------------------------------

    def insert_data(self, table: str, data: dict) -> Optional[Any]:
        """Insert a row into *table*. Returns the Supabase response or None."""
        if self.client is None:
            logger.debug("insert_data: client is None, returning None.")
            return None
        try:
            result = self.client.table(table).insert(data).execute()
            return result
        except Exception as exc:
            logger.error(f"insert_data error on table '{table}': {exc}")
            return None

    def query_data(
        self,
        table: str,
        query: Optional[dict] = None,
        columns: str = "*",
        limit: int = 100,
        offset: int = 0,
    ) -> List[dict]:
        """Query rows from *table* with optional filters and pagination.

        Args:
            table: Table name.
            query: Column-value pairs for exact-match filtering (``.match()``).
            columns: Columns to select (default ``"*"``).
            limit: Maximum number of rows to return.
            offset: Number of rows to skip.

        Returns:
            A list of row dicts (empty list on error or no client).
        """
        if self.client is None:
            logger.debug("query_data: client is None, returning [].")
            return []
        try:
            q = self.client.table(table).select(columns)
            if query:
                q = q.match(query)
            q = q.range(offset, offset + limit - 1)
            result = q.execute()
            return result.data if result and hasattr(result, "data") else []
        except Exception as exc:
            logger.error(f"query_data error on table '{table}': {exc}")
            return []

    def update_data(self, table: str, filters: dict, data: dict) -> Optional[Any]:
        """Update rows in *table* matching *filters* with *data*.

        Args:
            table: Table name.
            filters: Column-value pairs for exact-match filtering.
            data: Column-value pairs to update.

        Returns:
            The Supabase response or None.
        """
        if self.client is None:
            logger.debug("update_data: client is None, returning None.")
            return None
        try:
            result = self.client.table(table).update(data).match(filters).execute()
            return result
        except Exception as exc:
            logger.error(f"update_data error on table '{table}': {exc}")
            return None

    def delete_data(self, table: str, filters: dict) -> Optional[Any]:
        """Delete rows in *table* matching *filters*.

        Args:
            table: Table name.
            filters: Column-value pairs for exact-match filtering.

        Returns:
            The Supabase response or None.
        """
        if self.client is None:
            logger.debug("delete_data: client is None, returning None.")
            return None
        try:
            result = self.client.table(table).delete().match(filters).execute()
            return result
        except Exception as exc:
            logger.error(f"delete_data error on table '{table}': {exc}")
            return None
