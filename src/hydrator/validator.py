###############################################################################
# Copyright 2024 Google, LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
###############################################################################
# Copyright 2024 Google. This software is provided as-is, without warranty or
# representation for any use or purpose. Your use of it is subject to your
# agreement with Google.
import abc
import pathlib

from .process import Process
from .types import BaseConfig


# pylint: disable=too-few-public-methods,missing-function-docstring
class BaseValidator(abc.ABC):
    """Abstract base class for validators"""
    config: BaseConfig | None

    def __init__(self, name: str) -> None:
        self.valid: bool | None = None
        self.name = name

    @abc.abstractmethod
    async def run(self, config: BaseConfig | None = None,
                  hydrated_path: pathlib.Path | None = None) -> None:
        """Implement this method in concrete classes. This "runs" a validator"""
        pass  # pylint: disable=unnecessary-pass


class Gatekeeper(BaseValidator):
    """Implements a Gatekeeper validator, running Gator for local, static checks """
    constraint_paths: list[pathlib.Path]

    def __init__(self, *args, **kwargs):
        """GatekeeperValidator constructor takes a list of constraint paths
        which should be passed to Gator including, most importantly, the
        rendered manifest after hydration.  The gator binary name is hardcoded
        to 'gator' and it must be in the executing shell $PATH.

        Raises:
             ValueError: if provided constraint paths do not exist
        """
        super().__init__(*args, **kwargs)
        self.exitcode = None

    @classmethod
    def configure(cls, constraint_paths: list[pathlib.Path]):
        for path in constraint_paths:
            if not path.exists():
                raise ValueError(f'{path} does not exist')
        cls.constraint_paths = constraint_paths
        return cls

    def _build_constraint_paths(self) -> list[pathlib.Path]:
        """Adds additional constraint paths, looking inside state internal and
        returning a new list. If a provided template path contains a directory
        called `all`, it is presumed to contain Gator constraints whcih apply
        globally to all  groups and is added as a constraint path.  If
        a constraint path contains a folder named after the group,
        that is also added to the gator constraint paths.  If either of those
        cases is true, then the parent path is ignored in favor of the child
        path(s).

        Returns:
            a new list of constraint paths
        """
        new_paths = []
        group = self.config.group if self.config else None

        for path in self.constraint_paths:
            ignore_this_path = False
            if group:
                _, dirs, _ = next(path.walk())

                if group in dirs:
                    ignore_this_path = True
                    new_path = path.joinpath(group).resolve()
                    new_paths.append(new_path)

                if 'all' in dirs:
                    ignore_this_path = True
                    new_path = path.joinpath('all').resolve()
                    new_paths.append(new_path)

            if not ignore_this_path:
                new_paths.append(path.resolve())

        return new_paths

    async def run(self, config: BaseConfig | None = None,
                  hydrated_path: pathlib.Path | None = None) -> None:
        """Runs GatekeeperValidator; searches for gator in path, builds a list
        of constraint paths and builds the command that is passed to gator.
        The constraint_paths value is munged to produce a new value from
        `_build_constraint_paths` and is combined with the `hydrated_path` to
        produce a usefel set of manifests to test with gator.

        Args:
            config: config object
            hydrated_path: (ideally) full path to (a) hydrated manifest(s).

        Raises:
            CliWarning in one of the underlying calls, which we are letting
            bubble up
        """
        self.config = config

        cmd = ['gator', 'test']
        flag = '-f'

        const_paths = self._build_constraint_paths()
        for path in const_paths:
            cmd.extend([flag, str(path)])

        cmd.extend([flag, str(hydrated_path)])

        p = Process(cmd, logger_name='gatekeeper', name=self.name)
        await p.run()

        if p.proc.returncode == 0:  # type: ignore
            self.valid = True
        else:
            self.valid = False
