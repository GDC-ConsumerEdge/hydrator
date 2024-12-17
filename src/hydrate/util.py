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
import argparse
import hashlib
import logging
import os
import pathlib
import sys
import tarfile
import tempfile
import threading
from typing import IO, Self, Any

import aiofiles
import aioshutil
import jinja2

from .exc import CliWarning, ConfigWarning, ConfigError
from .types import BaseConfig


class TempDir:
    """Represents a dir which is intended to be ephemeral.  Intended to be used
    as an `argparse` argument type.

    Usage:
    ```
    # specify a temporary directory
    t = TempDir('foo/')
    t()         # creates temp dir
    t.cleanup() # all done

    # use system default w/ context manager:
    with TempDir():
      # do stuff
    ```
    """
    _temp: tempfile.TemporaryDirectory | None

    def __init__(self, path: str | None = None):
        if path is None:
            # pylint: disable-next=consider-using-with
            self._temp = tempfile.TemporaryDirectory()
            self.path = pathlib.Path(self._temp.name).resolve()
        else:
            self._temp = None
            self.path = pathlib.Path(path).resolve()
            if self.path.exists():
                raise argparse.ArgumentTypeError(
                    f"provided directory '{self.path}' already exists")

        self.path.mkdir(parents=True, exist_ok=True)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.cleanup()

    async def cleanup(self):
        """Cleans up (deletes) created directories"""
        if self._temp is not None:
            self._temp.cleanup()
        else:
            await aioshutil.rmtree(self.path, ignore_errors=True)


class LazyFileType(argparse.FileType):
    """Subclasses `argparse.FileType` in order to provide a way to lazily open
    files for reading/writing from arguments.  Initializes the same as the
    parent, but provides `open` method which returns the file object.

    Usage:
    ```
    parser = argparse.ArgumentParser()
    parser.add_argument('f', type=LazyFileType('w'))
    args = parser.parse_args()

    with args.f.open() as f:
        for line in foo:
            ...
    ```

    Provides an alternate constructor for use with the `default` kwarg to
    `ArgumentParser.add_argument`.

    Usage:
    ```
    parser.add_argument('-f', type=LazyFileType('w'),
                        default=LazyFileType.default('some_file.txt')
    ```
    """

    def __call__(self, string: str) -> Self:  # type: ignore
        self.filename = string  # pylint: disable=attribute-defined-outside-init

        if 'r' in self._mode or 'x' in self._mode:
            if not pathlib.Path(self.filename).exists():
                m = (f"can't open {self.filename}:  No such file or directory: "
                     f"'{self.filename}'")
                raise argparse.ArgumentTypeError(m)

        return self

    def open(self) -> IO:
        """Opens and returns file for reading
                :rtype: io.TextIOWrapper
        """
        return open(self.filename, self._mode, self._bufsize, self._encoding,
                    self._errors)

    @classmethod
    def default(cls, string: str, **kwargs) -> Self:
        """Alternate constructor for a default argument to argparse argument

        Args:
            string: filename to open
            **kwargs: arguments to `__init__`

        Returns:
            instance of `LazyFileType`
        """
        inst = cls(**kwargs)
        inst.filename = string  # pylint: disable=attribute-defined-outside-init
        return inst


def is_jinja_template(dest_file: pathlib.Path) -> bool:
    """Check if file is a jinja template

    Args:
        dest_file: file to check

    Returns:
        bool indicating if jinja template or not
    """
    return dest_file.suffix == '.j2'


# pylint: disable=too-few-public-methods
class LoggingMixin:
    """ Mixin class which adds logging functionality to a class. Not intended to be used as a
    subclass. To use, call `self._setup_logger; this creates an instance-internal logger at
    `self._logger`.  When setting up a logger, this expects this app's primary logger, the
    `hydrate` logger, to be already set up, and uses its level as the internal logger's level.
    Then, call `self.log` as a convenience method or, alternatively, the internal logger directly.
    """
    _logger: logging.Logger

    def _setup_logger(self, name: str, msg_prefix: str | None = None) -> None:
        """Sets up an internal logger object using a prescriptive convention

        Note that this tries to match the log level of the hydrate logger,
        because that's what is set up using CLI args.  Changes to the hydrate
        logger will need to be set here
        """
        self._prefix = msg_prefix
        self._logger = setup_logger(name, level=logging.getLogger('cli').level)

    def log(self, msg: str, lvl: str = 'error', **kwargs) -> None:
        """Convenience method for logging to the internal logger

        Args:
            msg: log message as a string
            lvl: log level as a string name, should be a method name off a
              `logging.Logger` object; i.e. `debug`, `info`, `warn`

        Returns:
            Result of logger method call; expected to be None
        """
        meth = getattr(self._logger, lvl)
        meth(f"{self._prefix + ": " if self._prefix else ""}{msg}", **kwargs)


async def template_string(template_str: str, cluster_config: dict, hydrator: LoggingMixin) -> str:
    """
    Render a Jinja template string and write it to a destination file.

    Args:
        template_str: Jinja template as a string.
        cluster_config: Cluster config dict.
        hydrator: Logger object for logging activities.

    Raises:
        jinja2.exceptions.TemplateError: If there are issues with template rendering.
    """
    try:

        template = jinja2.Template(
            template_str,
            autoescape=True,
            trim_blocks=True,
            lstrip_blocks=True,
            enable_async=True
        )
        return await template.render_async(**cluster_config)
    except jinja2.exceptions.TemplateError as e:
        hydrator.log(f'Error rendering template, contents: {template_str[:32]}...; '
                     f'error: {e}', 'exception')
        raise CliWarning('Error rendering template') from e


def setup_logger(name: str | None = None,
                 level: str | int = 'WARN',
                 log_format: str | None = None) -> logging.Logger:
    """Sets up an opinionated logger bifurcating on log level to send
    error and warning logs to stderr, info and debug to stdout

    Args:
        name: name of the logger to get; default `None` returns root logger
        level: level attribute in logging as a string
        log_format: optional log format; should be a valid format string,
            see std lib logging docs for more info

    Returns:
        configured `logging.Logger`
    """
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    if isinstance(level, int):
        logger.setLevel(level)
    else:
        logger.setLevel(getattr(logging, level))

    if log_format is None:
        log_format = "%(name)-10s %(levelname)-7s %(message)s"
    formatter = logging.Formatter(log_format)

    h1 = logging.StreamHandler(sys.stdout)
    h1.setLevel(logging.DEBUG)
    h1.setFormatter(formatter)
    h1.addFilter(lambda record: record.levelno <= logging.INFO)

    h2 = logging.StreamHandler()
    h2.setLevel(logging.WARNING)
    h2.setFormatter(formatter)

    logger.addHandler(h1)
    logger.addHandler(h2)

    return logger


def cap_word_to_snake_case(s: str) -> str:
    """Converts the (presumably) PEP8-ish class name (CapWord-style)
     to a snake-case name. Ex: `FooBarBaz` becomes `foo_bar_baz.

    Returns:
        str snake case name
    """
    results = []
    for i, char in enumerate(s):
        if char in ('A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'K',
                    'L', 'M', 'N', 'O', 'P', 'Q', 'R', 'S', 'T', 'U', 'V',
                    'W', 'X', 'Y', 'Z'):
            results.append(("_" if i != 0 else "") + char.lower())
        else:
            results.append(char)
    return "".join(results)


def package_oci_artifact(source_filepath: pathlib.Path,
                         output_filepath: pathlib.Path,
                         logger: logging.Logger | None = None) -> None:
    """Package artifact to be saved to an OCI repository

    Args:
        source_filepath: directory to package
        output_filepath: filename

    Raises:
        CliError if there are any issues encountered while packaging
    """
    try:
        if logger:
            logger.debug(f"Packaging file {source_filepath}")

        filename = os.path.basename(source_filepath)
        with tarfile.open(output_filepath, "w:gz") as tar:
            tar.add(source_filepath, arcname=filename)
    except FileNotFoundError as e:
        if logger:
            logger.exception(f"File {source_filepath} not found")
        raise CliWarning("File not found") from e
    except Exception as e:
        if logger:
            logger.exception(
                f'Generic exception caught packaging {source_filepath}',
                exc_info=e)
        raise CliWarning('Generic exception caught') from e


def check_config(cfg: BaseConfig) -> None:
    """ Unified config checking for incoming SoT data

    Args:
        cfg: config object to check

    Returns:
        None

    Raises:
        `ConfigWarning` if the config value can be skipped but app can continue;
        `ConfigError` if the config value is *so* invalid that the app should immediately exit
        non-zero
    """
    # TODO: add source file field and value validation
    # consider importing CSV validator models
    try:
        assert cfg.name.strip(), "empty"
    except AssertionError as e:
        raise ConfigWarning(f'Got empty string as {cfg.name_field}') from e
    except AttributeError as e:
        raise ConfigError(f"Source of truth file missing {cfg.name_field} column") from e

    try:
        assert cfg.group.strip() != "", (f"{cfg.group_field} is required and "
                                         "should not be empty")
    except AttributeError as e:
        raise ConfigError(f"Could not find column '{cfg.group_field}' in source") from e
    except AssertionError as e:
        raise ConfigError(e) from e

    try:
        cfg.tags  # pylint: disable
    except AttributeError as e:
        raise ConfigError(f"Could not find column '{cfg.tags_field}' in source") from e


def is_valid_object(obj: dict[str, Any]) -> bool:
    """ Returns boolean indicating whether provided dict is a valid object
    """
    required_keys = {'apiVersion', 'kind'}
    return (obj.keys() & required_keys) == required_keys


def sha256_digest(string: str | bytes) -> str:
    """ Returns SHA-256 hexdigest of provided string
    """
    if isinstance(string, str):
        string = string.encode("utf-8")

    return hashlib.sha256(string).hexdigest()


class SingletonMixin:
    """ Threading safe singleton mixin class.  Will run __init__ on subclasses for every
    invocation, so check self._initialized from __init__ to get around this if not desired.

    Use:
    class MyClass(SingletonMixin):
        def __init__(self):
          if not self._initialized:
             # initialize here
             self.foo = 'bar'
             # when done, set initialized to True
             self._initialized = True
    """
    _instances: dict[type, Any] = {}
    _lock = threading.Lock()
    _initialized: bool

    def __new__(cls):
        with SingletonMixin._lock:
            if cls not in SingletonMixin._instances:
                # Create instance
                instance = super().__new__(cls)
                SingletonMixin._instances[cls] = instance
                instance._initialized = False
        # Return existing instance
        return SingletonMixin._instances[cls]


class FileCache(dict, SingletonMixin):
    """Manages a cache of file paths with singleton behavior.

    This class extends the built-in dictionary to manage a cache specifically for file paths.
    It ensures that each key added to the cache is a valid pathlib.Path instance.
    The SingletonMixin ensures that only one instance of this class exists.

    Attributes:
        attribute1 (dict): Inherits from dict to store cached file paths.
        attribute2 (SingletonMixin): Ensures only one instance of the class.
    """

    def __setitem__(self, key, value) -> None:
        if not isinstance(key, pathlib.Path):
            raise TypeError(f'{key} is not a path')
        return super().__setitem__(key, value)


class InMemoryTextFile:
    """ A class to hold a file's contents in memory and maintain some metadata about it """

    def __init__(self, file_path: pathlib.Path,
                 /, encoding: str = 'utf-8') -> None:
        self.file_path = file_path
        self.encoding = encoding
        self.contents: str | None = None
        self.copy_path: pathlib.Path | None = None

    def __str__(self):
        return self.contents

    @classmethod
    async def from_file(cls, file_path: pathlib.Path,
                        /, encoding: str = 'utf-8') -> Self:
        """
        Reads file contents asynchronously and returns an instance of the class.

        This class method is used to create an instance of the class by reading the
        contents of the file specified by `file_path`. The file is read using the
        specified `encoding`. The method is asynchronous and uses aiofiles to
        perform non-blocking IO operations.

        Args:
            file_path (pathlib.Path): Path to the file to be read.
            encoding (str): Encoding to be used for reading the file. Defaults to 'utf-8'.

        Returns:
            Self: An instance of the class with `contents` attribute populated with
            the contents of the file.

        """
        inst = cls(file_path, encoding=encoding)
        async with aiofiles.open(file_path, 'r', encoding=inst.encoding) as f:
            inst.contents = await f.read()
        return inst
