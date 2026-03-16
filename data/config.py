from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseConfig(BaseSettings):
    """Database connection configuration loaded from environment variables.

    Reads DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD from the environment,
    falling back to local development defaults when not set.
    """

    model_config = SettingsConfigDict(extra="ignore")

    host: str = Field(default="localhost", validation_alias="DB_HOST")
    port: int = Field(default=5432, validation_alias="DB_PORT")
    database: str = Field(default="election_maps", validation_alias="DB_NAME")
    user: str = Field(default="election_maps", validation_alias="DB_USER")
    password: str = Field(default="election_maps_dev", validation_alias="DB_PASSWORD")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def url(self) -> str:
        return (
            f"postgresql://{self.user}:{self.password}"
            f"@{self.host}:{self.port}/{self.database}"
        )

    @classmethod
    def from_env(cls) -> "DatabaseConfig":
        """Load configuration from environment variables."""
        return cls()
