from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load .env from repo root (parent of this file's directory).
# python-dotenv expands variable references (e.g. $DB_PASSWORD) so
# composite values like DATABASE_URL=$USER:$PASSWORD@... work correctly.
# override=True means .env values take precedence over already-exported shell variables.
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)


class DatabaseConfig(BaseSettings):
    """Database connection configuration loaded from environment variables.

    The application uses a single local SQLite database. Set ``DATABASE_PATH``
    to point at the database file (defaults to ``/home/philiph/dbs/elections.db``).
    The live database is kept on local disk and snapshotted to Google Drive by
    ``backup_to_drive.sh`` — it must not be opened directly off the Drive mount.
    """

    model_config = SettingsConfigDict(extra="ignore")

    database_path: str = Field(
        default="/home/philiph/dbs/elections.db",
        validation_alias="DATABASE_PATH",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def url(self) -> str:
        """Return a SQLAlchemy SQLite connection URL for the configured path.

        Returns:
            str: Connection URL of the form ``sqlite:////absolute/path.db``.
        """
        return f"sqlite:///{self.database_path}"

    @classmethod
    def from_env(cls) -> "DatabaseConfig":
        """Load configuration from environment variables.

        Returns:
            DatabaseConfig: A new instance populated from the current environment.
        """
        return cls()
