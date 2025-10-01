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
import argparse
import asyncio
import collections
import csv
import logging
import multiprocessing
import pathlib
import pprint
import sys
# pylint: disable-next=no-name-in-module
from concurrent.futures import ProcessPoolExecutor, as_completed
from importlib.metadata import version
from logging.handlers import QueueListener
from typing import Type

from .exc import CliError, ConfigWarning, ConfigError
from .hydration import BaseHydrator, ClusterHydrator, GroupHydrator
from .oci_registry import OCIClientFactory
from .types import SotConfig, HydrateType, BaseConfig, GroupConfig, ClusterConfig, HydrationResult
from .util import (
    LazyFileType, TemporaryDirectory, cap_word_to_snake_case, check_config,
    configure_worker_logging, LevelBasedStreamHandler
)
from .validator import Gatekeeper



# pylint: disable-next=too-many-arguments,too-many-locals,too-many-branches
def hydration_worker(
        cls: Type[BaseHydrator],
        config: BaseConfig,
        log_queue: multiprocessing.Queue,
        log_level: int,
        temp_path: pathlib.Path,
        base_path: pathlib.Path,
        overlay_path: pathlib.Path,
        default_overlay: str | None,
        modules_path: pathlib.Path,
        hydrated_path: pathlib.Path,
        output_subdir: str,
        oci_registry: str | None,
        oci_tags: set[str],
        gatekeeper_validation: bool,
        gatekeeper_constraints: list[pathlib.Path],
        preserve_temp: bool,
        split_output: bool
) -> HydrationResult:
    """
    Top-level function to be executed by each worker process.

    This function is responsible for the full, end-to-end hydration of a single
    item (cluster or group). It initializes necessary components like validators
    and OCI clients, creates and runs a Hydrator instance, and returns a
    lightweight HydrationResult.

    Args:
        cls: The hydrator class to instantiate (ClusterHydrator or GroupHydrator).
        config: The configuration object for the specific item to hydrate.
        log_queue: The queue to which worker logs will be sent.
        log_level: The logging level for the worker process.
        All other arguments correspond to the CLI flags and configuration paths.

    Returns:
        A HydrationResult object summarizing the outcome of the task.
    """
    # The first step in the worker is to configure its logging.
    configure_worker_logging(log_queue, log_level)

    # Each worker process must configure its own validators and clients.
    validators = []
    if gatekeeper_validation:
        try:
            validators.append(
                Gatekeeper.configure(constraint_paths=gatekeeper_constraints))
        except ValueError as e:
            return HydrationResult(name=config.name, success=False,
                                   errors=[f"validator_setup_error: {e}"])

    oci_client = None
    if oci_registry:
        oci_client = OCIClientFactory.create_client(registry_url=oci_registry)

    hydrator = cls(
        config=config,
        temp=TemporaryDirectory(
            prefix=f"{config.name}_",
            dir=temp_path,
            delete=not preserve_temp),
        base_path=base_path,
        overlay_path=overlay_path,
        default_overlay=default_overlay,
        modules_path=modules_path,
        hydrated_path=hydrated_path,
        output_subdir=output_subdir,
        oci_client=oci_client,
        oci_tags=oci_tags,
        validators=validators,
        preserve_temp=preserve_temp,
        split_output=split_output,
    )

    # Each worker runs its own asyncio event loop for the hydration task.
    try:
        # The hydrator's run method is async, so we wrap it here.
        asyncio.run(hydrator.run())
    except Exception as e:  # pylint: disable=broad-exception-caught
        return HydrationResult(name=hydrator.name, success=False, errors=[f"runtime_error: {e}"])

    # Translate the final status of the hydrator into a lightweight result object.
    errors = []
    if not hydrator.status:
        if hydrator.status.jinja_ok is False:
            errors.append('jinja')
        if hydrator.status.kustomize_ok is False:
            errors.append('kustomize')
        if hydrator.status.split_ok is False:
            errors.append('split output')
        if hydrator.status.publish_ok is False:
            errors.append('OCI publish')
        if not hydrator.status.validators_ok:
            for v in hydrator.validated:
                if v.valid is False:
                    errors.append(cap_word_to_snake_case(v.__class__.__name__))
        if not hydrator.status.hydrator_ok:
            errors.append('hydrator checks')

    return HydrationResult(name=hydrator.name, success=bool(hydrator.status), errors=errors)


def parse_args() -> argparse.Namespace:
    """Parse command line arguments.  Normalizes some arguments, specifically
    provided `pathlib.Path`s.  Performs some argument validation and raises an
    error if any of the checks fail while logging errors to logging facility

    Raises:
        CliError: if any of the argument validation fails

    Returns:
        `argparse.Namespace` containing parsed args
    """
    root_parser = argparse.ArgumentParser()

    verbosity_mutex = root_parser.add_mutually_exclusive_group()
    verbosity_mutex.add_argument(
        '-v', '--verbose',
        action='count',
        default=0,
        help='increase output verbosity; -vv for max verbosity')
    verbosity_mutex.add_argument(
        '-q', '--quiet',
        action='store_true',
        help='output errors only')

    root_parser.add_argument(
        '--workers',
        type=int,
        default=0,
        help='Overrides the number of worker processes to use for hydration. '
             'The default (0) sets this value equal to the number of CPUs on the machine.'
    )
    root_parser.add_argument('--version', action='version', version=version('hydrator'))

    subparser = root_parser.add_subparsers(required=True)
    cluster = subparser.add_parser(
        'cluster',
        description='hydrate cluster-specific resources',
        help='hydrate cluster-specific resources'
    )
    group = subparser.add_parser(
        'group',
        description='hydrate group-specific resources',
        help='hydrate group-specific resources'
    )
    cluster.set_defaults(type=HydrateType.CLUSTER)
    group.set_defaults(type=HydrateType.GROUP)

    for p in (cluster, group):
        p.add_argument(
            'sot_file',
            metavar='source_of_truth_file.csv',
            type=LazyFileType('r'),
            help='file to read as source of truth')
        p.add_argument(
            '-m', '--modules',
            metavar='MODULES_DIR',
            type=pathlib.Path,
            default=pathlib.Path('./modules/'),
            help='path to modules; default: ./modules/')
        p.add_argument(
            '-b', '--base',
            metavar='BASE_DIR',
            type=pathlib.Path,
            default=pathlib.Path('base_library/'),
            help='path to base templates; default: base_library/')
        p.add_argument(
            '-o', '--overlay',
            metavar='OVERLAY_DIR',
            type=pathlib.Path,
            default=pathlib.Path('overlays/'),
            help='path to overlays; default: overlays/')
        p.add_argument(
            '-O', '--default-overlay',
            metavar='DEFAULT_OVERLAY',
            type=str,
            help='default overlay to use when one cannot be found')
        p.add_argument(
            '-y', '--hydrated',
            metavar='HYDRATED_OUTPUT_DIR',
            type=pathlib.Path,
            default=pathlib.Path('output'),
            help='path to render kustomize templates; default: $PWD/output')
        p.add_argument(
            '--oci-registry',
            help='target registry to upload OCI artifacts')
        p.add_argument(
            '--oci-tags',
            default='latest',
            help='Comma-separated list of tags to apply to OCI uploads',
            type=str)
        p.add_argument(
            '--gatekeeper-validation',
            action='store_true',
            default=False,
            help='whether to use Gatekeeper validation'
        )
        p.add_argument(
            '--gatekeeper-constraints',
            action='append',
            type=pathlib.Path,
            help='path(s) to Gatekeeper constraints; may be use more than once; '
                 'defaults: validation-gatekeeper/constraints, '
                 'validation-gatekeeper/template-library'
        )
        p.add_argument(
            '-t', '--temp',
            metavar='TEMP_DIR',
            type=pathlib.Path,
            help='path to temporary workdir; default: uses system temp')
        p.add_argument(
            '--preserve-temp',
            action='store_true',
            default=False,
            help='whether to preserve temporary workdir; default: false'
        )

    # output style should be a mutex for cluster hydration
    output_mutex = cluster.add_mutually_exclusive_group()
    output_mutex.add_argument(
        '-s', '--output-subdir',
        choices=('group', 'cluster', 'none'),
        default='group',
        help='type of output subdirectory to create; default: group')
    output_mutex.add_argument(
        '--split-output',
        action='store_true',
        default=False,
        help='whether to split the generated manifest into multiple files; default: false'
    )

    # output style is more rigid for group hydration
    # set output subdir should be none by default, but enabling split output is appropriate
    group.set_defaults(output_subdir='none')
    group.add_argument(
        '--split-output',
        action='store_true',
        default=False,
        help='whether to split the generated manifest into multiple files; default: false'
    )

    # these selectors are only appropriate for cluster hydration
    selector_mutex = cluster.add_mutually_exclusive_group()
    selector_mutex.add_argument(
        '--cluster-name',
        metavar='CLUSTER_NAME',
        action='append',
        help='name of cluster to select from config; may be used more than once')
    selector_mutex.add_argument(
        '--cluster-tag',
        metavar='CLUSTER_TAG',
        action='append',
        help='tag to use to select clusters from config; may be used more than once')
    selector_mutex.add_argument(
        '--cluster-group',
        metavar='CLUSTER_GROUP',
        action='append',
        help='name of cluster group to select from config; may be used more than once')

    # these selectors are only for group hydration
    pkg_select_mutex = group.add_mutually_exclusive_group()
    pkg_select_mutex.add_argument(
        '--group',
        metavar='GROUP',
        action='append',
        help='name of group to select from config; may be used more than once')
    pkg_select_mutex.add_argument(
        '--tag',
        metavar='TAG',
        action='append',
        help='tag to use to select groups from config; may be used more than once')

    args = root_parser.parse_args()

    args = validate_and_normalize_args(args)

    return args


def validate_and_normalize_args(args: argparse.Namespace) -> argparse.Namespace:
    """ Validate and normalize command line arguments.
    Args:
        args: argparse.Namespace parsed command line arguments

    Returns:
        `argparse.Namespace` containing args

    Raises:
        CliError: if any of the argument validation fails
    """
    # set default values for gatekeeper constraints if not defined
    if args.gatekeeper_validation and not args.gatekeeper_constraints:
        args.gatekeeper_constraints = [
            pathlib.Path('validation-gatekeeper/constraints'),
            pathlib.Path('validation-gatekeeper/template-library')]

    # turn all provided paths into fully-resolved absolute paths
    args.modules = args.modules.resolve()
    args.base = args.base.resolve()
    args.overlay = args.overlay.resolve()
    args.hydrated = args.hydrated.resolve()

    # tags should be a set
    if args.type is HydrateType.CLUSTER and args.cluster_tag:
        args.cluster_tag = set(args.cluster_tag)

    # if oci registry and oci tags are set, parse oci tags
    if args.oci_registry and args.oci_tags:
        args.oci_tags = {tag.strip() for tag in args.oci_tags.split(",")}

    for item in (args.base, args.overlay):
        try:
            assert item.exists(), (
                f'Provided path ({item}) does not exist')
            assert item.is_dir(), (
                f'Provided path ({item}) is not a directory')
            assert not item.is_file(), (
                f'Provided path ({item}) is not a directory')
        except AssertionError as e:
            print(e, file=sys.stderr)
            raise CliError(e) from e

    return args


# pylint: disable-next=too-many-instance-attributes
class BaseCli:
    """ Implements the base CLI flow for two subcommands, group and cluster hydration.
    This is a base class and is not expected to be instantiated directly; things will break if so.
    """
    _config: SotConfig
    _groups: set[str]
    _names: set[str]
    _tags: set[str]
    results: list[HydrationResult]
    _tasks_submitted: int

    # pylint: disable-next=too-many-arguments,too-many-locals
    def __init__(self, *,
                 logger: logging.Logger,
                 sot_file: LazyFileType,
                 temp_path: pathlib.Path,
                 base_path: pathlib.Path,
                 overlay_path: pathlib.Path,
                 default_overlay: str | None,
                 modules_path: pathlib.Path,
                 hydrated_path: pathlib.Path,
                 output_subdir: str,
                 gatekeeper_validation: bool,
                 gatekeeper_constraints: list[pathlib.Path],
                 oci_registry: str,
                 oci_tags: set[str],
                 hydration_type: HydrateType,
                 preserve_temp: bool,
                 split_output: bool,
                 workers: int):
        if self.__class__ is BaseCli:
            raise TypeError("BaseCli cannot be directly instantiated")

        self._logger = logger
        self._sot_file = sot_file
        self._temp = temp_path
        self._base_path = base_path
        self._overlay_path = overlay_path
        self._default_overlay = default_overlay
        self._modules_path = modules_path
        self._hydrated_path = hydrated_path
        self._output_subdir = output_subdir
        self._gatekeeper_validation = gatekeeper_validation
        self._gatekeeper_constraints = gatekeeper_constraints
        self._oci_registry = oci_registry
        self._oci_tags = oci_tags
        self._hyd_type = hydration_type
        self._preserve_temp = preserve_temp
        self._split_output = split_output
        self._workers = workers

    def report(self) -> dict[str, list[str]]:
        """Creates a pretty-printable summary of total hydration state

        Returns:
            results from `gather_failures`, dict `{item: [errors]}`
        """
        failures = self._gather_failures()
        if failures:
            unsuccessful = len(failures)
            successful = self._tasks_submitted - unsuccessful
            print(f'\nTotal {self._tasks_submitted} '
                  f'{self._hyd_type.value}s - {successful} rendered '
                  f'successfully, {unsuccessful} unsuccessful\n', file=sys.stderr)
        else:
            print(f'{self._tasks_submitted} {self._hyd_type.value}s total, all rendered '
                  f'successfully')

        if failures:
            for n, errs in failures.items():
                print(f'{self._hyd_type.value.title()} {n} failed: {", ".join(errs)}',
                      file=sys.stderr)

        return failures

    def _gather_failures(self) -> dict[str, list[str]]:
        """Checks the status of hydrated and validated items and constructs
        a mapping of their failure states to dump to the console/log

        Returns:
            dict in form of `{item: [errors]}`
        """
        failures = collections.defaultdict(list)
        for result in self.results:
            if not result.success:
                failures[result.name].extend(result.errors)
        return failures

    def _filter_item(self, item: str, item_config: BaseConfig) -> bool:
        if self._names:
            if item_config.name not in self._names:
                self._logger.debug(f"{self._hyd_type.value.title()} '{item}': not in "
                                   f"provided name selectors; not hydrating...")
                return True

        if self._groups:
            if item_config.group not in self._groups:
                self._logger.debug(f"{self._hyd_type.value.title()} '{item}': not in "
                                   f"'provided group selectors; not hydrating...")
                return True

        # if tags provided as args
        if self._tags:
            try:
                # split config tags into a set
                config_tags = {t.strip() for t in item_config.tags.split(",")}
            except KeyError:
                self._logger.warning(f"{self._hyd_type.value.title()} '{item}': specified tags as "
                                     f"args but has none in config file")
                return True

            # if config tags don't intersect with tags from args,
            # then don't hydrate item
            if not config_tags.intersection(self._tags):
                self._logger.debug(f"{self._hyd_type.value.title()} '{item}': no matching tags; "
                                   f"not hydrating.")
                return True

        return False

    def _hydrate(self, config_data: SotConfig) -> None:
        """
        Manages the parallel hydration of resources using a process pool.

        This method determines the correct Hydrator class, then iterates through
        the source-of-truth data, submitting a task to the ProcessPoolExecutor
        for each item that is not filtered out. It then collects the results
        as they are completed.

        To ensure logs from worker processes are not lost, this method also sets
        up a multiprocessing-safe logging queue and a `QueueListener`. The
        listener forwards logs from the workers to the main process's logger in
        real-time. The listener is stopped automatically when hydration is complete.

        Args:
            config_data: A dictionary containing the configuration for all items
                         to be processed.
        """
        cls: Type[BaseHydrator]
        if self._hyd_type is HydrateType.CLUSTER:
            cls = ClusterHydrator
        elif self._hyd_type is HydrateType.GROUP:
            cls = GroupHydrator
        else:
            raise CliError(f'Unknown hydration type {self._hyd_type}')

        # Set up a queue and a listener for logs from worker processes.
        log_queue = multiprocessing.Manager().Queue()
        listener = QueueListener(log_queue, LevelBasedStreamHandler())
        listener.start()

        try:
            # Use ProcessPoolExecutor to manage a pool of long-lived worker processes.
            max_workers = self._workers if self._workers > 0 else None
            with ProcessPoolExecutor(max_workers=max_workers) as executor:
                futures = {}
                for c, cfg in config_data.items():
                    if self._filter_item(c, cfg):
                        continue

                    self._logger.debug(f'Submitting hydration task for {c}')
                    future = executor.submit(
                        hydration_worker,
                        cls,
                        cfg,
                        log_queue,  # type: ignore
                        self._logger.level,
                        self._temp,
                        self._base_path,
                        self._overlay_path,
                        self._default_overlay,
                        self._modules_path,
                        self._hydrated_path,
                        self._output_subdir,
                        self._oci_registry,
                        self._oci_tags,
                        self._gatekeeper_validation,
                        self._gatekeeper_constraints,
                        self._preserve_temp,
                        self._split_output,
                    )
                    futures[future] = cfg.name

                self._tasks_submitted = len(futures)
                self._logger.debug(f'Submitted {self._tasks_submitted} tasks to workers.')

                # Process results as they complete to provide responsive feedback.
                for future in as_completed(futures):
                    name = futures[future]
                    try:
                        result = future.result()
                        self.results.append(result)
                        if result.success:
                            self._logger.info(f'Successfully hydrated {name}')
                        else:
                            self._logger.warning(
                                f'Hydration failed for {name}: {", ".join(result.errors)}')
                    except Exception as e:  # pylint: disable=broad-exception-caught
                        self._logger.error(
                            f"Hydration for {name} generated an exception in the worker: {e}")
                        self.results.append(
                            HydrationResult(
                                name=name,
                                success=False,
                                errors=[f"worker_exception: {e}"]
                            )
                        )
        finally:
            # Stop the logging listener once all tasks are complete.
            listener.stop()

    def _process_sot_file(self) -> SotConfig:
        """Takes a file-like object; returns a dictionary where the key is the
        item name, the value is a dict with the item's configuration.
        Skips rows without an item name because that is the key to the
        returned dict.

        Raises:
            CliError: any failed check raises CliError; all other
                exceptions are unexpected

        Returns:
            parsed config as dict in the form of
            {item_name: {config: ...}, ...}
        """
        data: SotConfig = {}
        dcls = ClusterConfig if self._hyd_type is HydrateType.CLUSTER else GroupConfig
        with self._sot_file.open() as sot_f:
            self._logger.debug(f"Processing source of truth file: {sot_f.name}")
            reader = csv.DictReader(sot_f, dialect='excel')
            row: dict[str, str]
            for row in reader:
                try:
                    cfg = dcls({k.strip(): v.strip() for k, v in row.items() if row})
                except AttributeError as e:
                    self._logger.error(f'Check source of truth format; skipping line '
                                       f'{reader.line_num} with error: {e}')
                    continue

                self._logger.debug(f'Got config from CSV:\n{pprint.pformat(cfg)}')

                try:
                    check_config(cfg)
                except ConfigWarning as e:
                    self._logger.warning(f"Skipping line {reader.line_num}: {e}")
                    del data[cfg.name]
                except ConfigError as e:
                    self._logger.error(e)
                    raise CliError(e) from e

                data[cfg.name] = cfg

        self._config = data
        return self._config

    def run(self) -> int:
        """Runs the CLI workflow

        Returns:
            exit code as int
        """
        # get config from source-of-truth file
        try:
            config_data = self._process_sot_file()
        except CliError:
            return 1

        self.results = []
        self._tasks_submitted = 0

        # The main hydration logic is now a synchronous, blocking call that
        # manages the lifecycle of the process pool.
        self._hydrate(config_data)

        failures = self.report()

        if failures:
            return 1
        return 0


# pylint: disable-next=too-many-instance-attributes,too-few-public-methods
class ClusterCli(BaseCli):
    """ CLI class for cluster hydration """

    def __init__(self, *,
                 cluster_name: set[str],
                 cluster_tag: set[str],
                 cluster_group: set[str],
                 **kwargs):
        super().__init__(**kwargs)
        self._names = cluster_name
        self._groups = cluster_group
        self._tags = cluster_tag


class GroupCli(BaseCli):
    """ CLI class for group hydration """

    def __init__(self, *,
                 groups: set[str],
                 tags: set[str],
                 **kwargs):
        super().__init__(**kwargs)
        self._names = groups
        self._groups = groups
        self._tags = tags
