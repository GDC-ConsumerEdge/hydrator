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
import asyncio
import asyncio.subprocess
import shutil

from .exc import CliWarning
from .util import LoggingMixin


class Process(LoggingMixin):
    """ Implements an asynchronous process via asyncio subprocessing with internal logging """

    def __init__(self, command: list[str],
                 logger_name: str | None = None,
                 name: str = "",
                 store_output: bool = True) -> None:
        self.cmd = command
        self.complete = False
        self.proc: asyncio.subprocess.Process | None = None  # pylint: disable=no-member
        self.store_output = store_output
        self.stdout: list[str] = []
        self.stderr: list[str] = []
        self._setup_logger(name=logger_name if logger_name else self.cmd[0].lower(),
                           msg_prefix=name)

    def _find_in_path(self) -> None:
        """Searches the PATH for `self.cmd[0]` and raises a CliWarning if it can't be
        found, replacing the original value at `self.cmd[0]`

        Raises:
            CliWarning: if binary can't be found

        Returns:
            command as a list with bin path as first (and only) element
        """
        bin_path = shutil.which(self.cmd[0])
        if bin_path is None:
            err = f'Could not find {self.cmd[0]} in the path'
            self.log(err)
            raise CliWarning(err)
        self.cmd[0] = bin_path

    async def run(self, **kwargs) -> None:
        """ Runs the process asynchronously. Stores the created process to `self.proc` and sets
        `self.complete` to `True` when done.  stdout and stderr are captured and logged to
        the internal logger.  If `self.store_output` is truthy, the stdout and stderr are saved
        to eponymous instance attributes.

        Args:
            **kwargs: kwargs passed directly to `asyncio.create_subprocess_exec`

        Returns:
            None
        """
        self._find_in_path()
        self.log(f'Running {self.cmd[0]}', 'info')
        self.log(f'Running command: {' '.join(self.cmd)}', 'debug')

        self.proc = await asyncio.create_subprocess_exec(
            *self.cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **kwargs
        )

        await self.proc.wait()
        self.complete = True

        for reader, level, internal in ((self.proc.stdout, 'info', self.stdout),
                                        (self.proc.stderr, 'warn', self.stderr)):
            if reader is None:
                continue
            async for bytes_ in reader:
                decoded = bytes_.decode('utf-8').rstrip('\n')
                self.log(decoded, level)
                if self.store_output:
                    internal.append(decoded)

        if isinstance(self.proc.returncode, int) and self.proc.returncode == 0:
            self.log(
                f'{self.cmd[0]} completed successfully with exitcode {self.proc.returncode}',
                'info')
        if isinstance(self.proc.returncode, int) and self.proc.returncode > 0:
            self.log(
                f'{self.cmd[0]} exited with {self.proc.returncode}',
                'warn')
