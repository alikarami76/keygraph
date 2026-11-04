"""SQLite (and any other SQLAlchemy dialect) through the inspector. Metadata only; samples are bounded SELECTs."""

from __future__ import annotations

from ..probe import SelectOnlyRunner
from .base import Source
from .live import scan_url


class SQLAlchemySource(Source):
    kind = "sqlalchemy"

    def __init__(self, url: str, schemas=None, all_schemas: bool = False):
        self.url, self.schemas, self.all_schemas = url, schemas, all_schemas
        self.dialect = "mysql" if url.startswith("mysql") else "postgres" if url.startswith("postgres") else "sqlite"
        self.quote = "`" if self.dialect == "mysql" else '"'
        self._runner = SelectOnlyRunner.from_url(url)
        self.run = self._runner.run

    def schema(self):
        return scan_url(self.url, self.schemas, self.all_schemas)


SQLiteSource = SQLAlchemySource
