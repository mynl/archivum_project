"""
Manage library for archivum.

Equivalent to and based on manager module in file_database.

Querying uses a file-database project-like combo regex-sql (querex) querier.
"""

"""Manage config file and index database creation and updating."""

from datetime import datetime
from pathlib import Path
import re
import socket
import subprocess
import time
import yaml

import pandas as pd

from . import BASE_DIR, APP_SUFFIX
from . querier import query_ex, query_help as query_help_work
from . disk_info import DriveInfo
from . hasher import hash_many


class Library():
    """Library specified by config yaml (archivum-config) file."""

    def __init__(self, config_path: Path):
        """
        Load YAML config from file.

        The archivum-config suffix optional and added if missing.
        If not found in current directory, looks in local (eg. for default config).
        """
        self.config_path = Path(config_path)
        if self.config_path.suffix != APP_SUFFIX:
            self.config_path = self.config_path.with_suffix(APP_SUFFIX)
        # print(self.config_path)
        if not self.config_path.exists():
            self.config_path = BASE_DIR / self.config_path.name
        # print(self.config_path)
        with self.config_path.open() as f:
            self._config = yaml.safe_load(f)
            self.hostname = socket.gethostname()
            if self.hostname.lower() != self._config['hostname'].lower():
                print(
                    f"WARNING: Host name {self.hostname} of machine does not match config file {cfg['hostname']}."
                )
        self._database = pd.DataFrame([])
        self._config_df = None
        self._last_query = None
        self._last_unrestricted = 0
        self._last_query_title = ''
        self._last_query_expr = ''

    def __getattr__(self, name):
        """Provide access to config yaml dictionary."""
        if name in self._config:
            return self._config[name]
        raise AttributeError(
            f"{type(self).__name__!r} object has no attribute {name!r}")

    def __getitem__(self, name):
        """Access to values of config dictionary."""
        return self._config[name]

    def __repr__(self):
        """Create simple string representation."""
        return f'PM({self.config_path.name})'

    @property
    def config(self):
        """Return the config yaml dictionary."""
        return self._config

    @property
    def config_df(self):
        if self._config_df is None:
            self._config_df = pd.Series(self.config).to_frame('value')
            self._config_df.index.name = 'key'
        return self._config_df

    def set_attributes(self, **kwargs):
        """Set new attributes of config yaml dictionary."""
        for k, v in kwargs.items():
            self._config[k] = v

    def query(self, expr):
        """Run ``expr`` through the querier."""
        self._last_query_expr = expr
        self._last_query, self._last_unrestricted = query_ex(self.database, expr)
        self._last_query_title = f'<strong>QUERY</strong>: <code>{expr}</code>, showing {len(self._last_query)} of {self._last_unrestricted} results.'
        return self._last_query

    @staticmethod
    def query_help():
        """Print help for query syntax."""
        return query_help_work()

    def duplicates(self, keep=False) -> pd.DataFrame:
        """
        Return rows that share the same hash (i.e., duplicate content).

        keep = 'first', 'last', False: keep first, last or all duplicates
        """
        df = self.database
        return df[df.duplicated("hash", keep=keep)].sort_values("hash")

    def save(self):
        """Save dictionary to yaml."""
        backup = self.config_path.with_suffix(APP_SUFFIX  + '-bak')
        if backup.exists():
            backup.unlink()
        backup.hardlink_to(self.config_path)
        self.config_path.unlink()
        with self.config_path.open("w") as f:
            yaml.safe_dump(self._config, f,
                           sort_keys=False,                # preserve input order
                           default_flow_style=False,       # block structure
                           width=100,
                           indent=2
                           )
        self.database.to_feather(self.database_path)

    @property
    def database_path(self):
        """Get the database path name from config file."""
        return Path(self._config['database'])

    @property
    def database(self):
        """Return the database."""
        if self._database.empty:
            if self.database_path.exists():
                self._database = pd.read_feather(self.database_path)
        return self._database

    # def schedule(self, execute=False):
    #     """Set up the task schedule for the project."""
    #     schedule_time = self.config.get('schedule_time', '')
    #     if schedule_time == "":
    #         print('Scheduling not defined in config file. Exiting.')
    #     schedule_frequency = self.schedule_frequency
    #     task_name = f'file-db-task {self.project}'
    #     cmd = [
    #         "schtasks",
    #         "/Create",
    #         "/TN", task_name,
    #         "/TR", f'file-db index -c "{str(self.config_path)}"',
    #         "/SC", schedule_frequency,
    #         "/ST", schedule_time,
    #         "/F"  # force update if exists
    #     ]

    #     if execute:
    #         print('Executing:\n\n', ' '.join(cmd))
    #         subprocess.run(cmd, check=True)
    #     else:
    #         print('Would execute\n\n', ' '.join(cmd))

    @staticmethod
    def list():
        """List projects in the default location."""
        # TODO
        return list(BASE_DIR.glob('*' + APP_SUFFIX))

    @staticmethod
    def list_deets():
        """Dataframe of all projects in default location."""
        # not sure what the best "way around" is for this...
        df = pd.concat(
            [ProjectManager(p).config_df for p in ProjectManager.list()],
            axis=1).T.fillna('')
        df = df.set_index('project').T
        return df
