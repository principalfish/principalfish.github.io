import os
from dataclasses import dataclass


@dataclass
class DatabaseConfig:
    host: str = "localhost"
    port: int = 5432
    database: str = "election_maps"
    user: str = "election_maps"
    password: str = "election_maps_dev"

    @property
    def url(self) -> str:
        return (
            f"postgresql://{self.user}:{self.password}"
            f"@{self.host}:{self.port}/{self.database}"
        )

    @classmethod
    def from_env(cls) -> "DatabaseConfig":
        return cls(
            host=os.getenv("DB_HOST", "localhost"),
            port=int(os.getenv("DB_PORT", "5432")),
            database=os.getenv("DB_NAME", "election_maps"),
            user=os.getenv("DB_USER", "election_maps"),
            password=os.getenv("DB_PASSWORD", "election_maps_dev"),
        )
