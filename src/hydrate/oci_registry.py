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
# pylint: disable=duplicate-code
import abc
import io
import logging
import pathlib
import selectors
import shutil
import subprocess
from typing import Set

from .exc import CliWarning
from .util import cap_word_to_snake_case, setup_logger


# pylint: disable-next=too-few-public-methods
class OCIClient(abc.ABC):
    """Abstract base class for OCI clients"""
    _logger: logging.Logger

    def __init__(self, registry_url) -> None:
        self._registry_url: str = registry_url
        self.valid: bool | None = None
        self._setup_logger()

    def _setup_logger(self) -> None:
        """Sets up an internal logger object using a prescriptive convention

        Note that this tries to match the log level of the hydration logger,
        because that's what is set up using CLI args.  Changes to the hydration
        logger will need to be set here
        """
        self._logger = setup_logger(
            level=logging.getLogger('hydration').level,
            name=cap_word_to_snake_case(self.__class__.__name__))

    @abc.abstractmethod
    def push(self, artifact_path: pathlib.Path,
             image_name: str, image_tags: Set[str]) -> None:
        """Push artifact to configured OCI repo"""


# pylint: disable-next=too-few-public-methods
class OrasCliClient(OCIClient):
    """OCI Client using ORAS CLI"""
    _command = "oras"

    def __init__(self, registry_url: str):
        super().__init__(registry_url=registry_url)

    def _get_command(self) -> list[str]:
        """Searches the PATH for `self.cmd` and raises a CliWarning if it
        can't be found.  It's presumed the return value is passed to a
        `subprocess` construct and therefore begins building a list

        Raises:
            CliWarning: if binary can't be found

        Returns:
            command as a list with bin path as first (and only) element
        """
        bin_path = shutil.which(self._command)
        if bin_path is None:
            err = f'Could not find {self._command} in the path'
            self._logger.error(err)
            raise CliWarning(err)
        return [bin_path]

    def push(self, artifact_path: pathlib.Path,
             image_name: str, image_tags: Set[str]) -> None:
        command = self._get_command()
        artifact_reference = (
            f"{self._registry_url}/{image_name}:"
            f"{",".join(image_tags)}"
        )

        command.extend(
            [
                "push",
                artifact_reference,
                str(artifact_path),
                "--disable-path-validation"  # required when using absolute path
            ]
        )

        self._run_command(command)

    def _run_command(self, cmd: list[str]) -> int:
        """Runs oras using `subprocess.Popen` function.  Grabs
        stdout and stderr and logs them to the internal logger.  If the process
        exits with a non-zero exit code, the command is presumed to have
        "failed", and it sets the internal state `self.valid` to False.

        Args:
            cmd: A command to run, as would be passed as an argument to the
              `subprocess.Popen` constructor

        Returns:
            Process exit code as an int
        """
        self._logger.info(f'Running {self._command}')
        self._logger.debug(f'Running command: {' '.join(cmd)}')

        # TODO: duplicate code; disable module-level pylint disable and recheck
        # pylint: disable-next=consider-using-with
        p = subprocess.Popen(
            cmd,
            bufsize=1,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE)

        selector = selectors.DefaultSelector()
        if p.stdout:
            selector.register(p.stdout, selectors.EVENT_READ)  # typing: disable
        if p.stderr:
            selector.register(p.stderr, selectors.EVENT_READ)  # typing: disable

        while True:
            for key, _ in selector.select():
                assert isinstance(key.fileobj, io.TextIOWrapper)
                while line := key.fileobj.readline():
                    if key.fileobj is p.stdout:
                        self._logger.debug(line.strip('\n'))
                    else:
                        self._logger.error(line.strip('\n'))

            # check to see if the process is complete and log status if so
            # if not complete, continue the loop
            if p.poll() is None:
                continue

            if p.returncode == 0:
                self.valid = True
                self._logger.info(
                    f'{self._command} completed successfully with '
                    f'exitcode {p.returncode}')
            if p.returncode > 0:
                self.valid = False
                self._logger.error(
                    f'{self._command} exited with {p.returncode}')

            return p.returncode


# pylint: disable-next=too-few-public-methods
class OCIClientFactory:
    """OCI Client creation factory"""

    # only ORAS CLI Client supported for now
    @staticmethod
    def create_client(registry_url: str):
        """Create client"""
        return OrasCliClient(registry_url=registry_url)
