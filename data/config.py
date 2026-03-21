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

    Supabase (remote) mode — set these three vars:
        SUPABASE_REGION=aws_region
        SUPABASE_DB_USERNAME=your_username
        SUPABASE_DB_PASSWORD=your_password

    Local Docker mode (default when SUPABASE_HOST is not set):
        DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
    """

    model_config = SettingsConfigDict(extra="ignore")

    # Supabase pooler connection (takes priority when all three are set)
    supabase_region: str | None = Field(default=None, validation_alias="SUPABASE_REGION")
    supabase_db_username: str | None = Field(default=None, validation_alias="SUPABASE_DB_USERNAME")
    supabase_db_password: str | None = Field(default=None, validation_alias="SUPABASE_DB_PASSWORD")

    # Local Docker fallback
    host: str = Field(default="localhost", validation_alias="DB_HOST")
    port: int = Field(default=5432, validation_alias="DB_PORT")
    database: str = Field(default="election_maps", validation_alias="DB_NAME")
    user: str = Field(default="election_maps", validation_alias="DB_USER")
    password: str = Field(default="election_maps_dev", validation_alias="DB_PASSWORD")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def url(self) -> str:
        """Return a SQLAlchemy-compatible PostgreSQL connection URL.

        If SUPABASE_REGION and SUPABASE_DB_USERNAME are set, constructs a Supabase
        session-pooler URL using SUPABASE_DB_PASSWORD.
        Otherwise falls back to the local DB_* variables.

        Returns:
            str: Connection URL in the form
                ``postgresql://user:password@host:port/database``.
        """
        if self.supabase_region and self.supabase_db_username and self.supabase_db_password:
            host = f"{self.supabase_region}.pooler.supabase.com"
            return (
                f"postgresql://{self.supabase_db_username}:{self.supabase_db_password}"
                f"@{host}:5432/postgres"
            )
        return (
            f"postgresql://{self.user}:{self.password}"
            f"@{self.host}:{self.port}/{self.database}"
        )

    @classmethod
    def from_env(cls) -> "DatabaseConfig":
        """Load configuration from environment variables.

        Returns:
            DatabaseConfig: A new instance populated from the current environment.
        """
        return cls()

    @classmethod
    def local(cls) -> "DatabaseConfig":
        """Return a config for the local Docker PostgreSQL, ignoring any Supabase env vars.

        Uses model_construct to bypass pydantic-settings env var reading entirely,
        so SUPABASE_* vars in the environment are never picked up.
        Use this in scripts that must always write to local postgres (e.g. model runners).

        Returns:
            DatabaseConfig: A config instance with hardcoded local Docker defaults.
        """
        return cls.model_construct(
            supabase_region=None,
            supabase_db_username=None,
            supabase_db_password=None,
            host="localhost",
            port=5432,
            database="election_maps",
            user="election_maps",
            password="election_maps_dev",
        )
