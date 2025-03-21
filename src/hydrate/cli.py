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
import pathlib
import pprint
import sys
from typing import Set

from .exc import CliError, ConfigWarning, ConfigError
from .hydration import BaseHydrator, ClusterHydrator, PackageHydrator
from .oci_registry import OCIClientFactory
from .types import SotConfig, HydrateType, BaseConfig, PackageConfig, ClusterConfig
from .util import LazyFileType, TempDir, \
    cap_word_to_snake_case, check_config
from .validator import BaseValidator, Gatekeeper


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
        help='When set uses async workers to hydrate resources; defaults to sync hydration. '
             'Recommend starting at 25'
    )

    subparser = root_parser.add_subparsers(required=True)
    cluster = subparser.add_parser(
        'cluster',
        description='hydrate cluster-specific resources',
        help='hydrate cluster-specific resources'
    )
    package = subparser.add_parser(
        'package',
        description='hydrate resources for a package',
        help='hydrate resources for a package'
    )
    cluster.set_defaults(type=HydrateType.CLUSTER)
    package.set_defaults(type=HydrateType.PACKAGE)

    for p in (cluster, package):
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
            '-t', '--temp',
            metavar='TEMP_DIR',
            type=TempDir,
            default=TempDir(),
            help='path to temporary workdir; default: uses system temp')
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

    # output style is more rigid for package hydration
    # set output subdir should be none by default, but enabling split output is appropriate
    package.set_defaults(output_subdir='none')
    package.add_argument(
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
        help='name of cluster to select from config')
    selector_mutex.add_argument(
        '--cluster-tag',
        metavar='CLUSTER_TAG',
        action='append',
        help='tag to use to select clusters from config; may be used more than '
             'once')
    selector_mutex.add_argument(
        '--cluster-group',
        metavar='CLUSTER_GROUP',
        help='name of cluster group to select from config')

    # these selectors are only for package hydration
    pkg_select_mutex = package.add_mutually_exclusive_group()
    pkg_select_mutex.add_argument(
        '--group',
        metavar='GROUP',
        help='name of group to select from config')
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
    """ Implements the base CLI flow for two subcommands, package and cluster hydration.
    This is a base class and is not expected to be instantiated directly; things will break if so.
    """
    _validators: list[BaseValidator]
    hydrators: list[BaseHydrator]
    _config: SotConfig
    _name: str
    _group: str
    _tags: Set[str]

    # pylint: disable-next=too-many-arguments,too-many-locals
    def __init__(self, *,
                 logger: logging.Logger,
                 sot_file: LazyFileType,
                 temp: TempDir,
                 base: pathlib.Path,
                 overlay: pathlib.Path,
                 modules: pathlib.Path,
                 hydrated: pathlib.Path,
                 output_subdir: str,
                 gatekeeper_validation: bool,
                 gatekeeper_constraints: list[pathlib.Path],
                 oci_registry: str,
                 oci_tags: Set[str],
                 hydration_type: HydrateType,
                 preserve_temp: bool,
                 split_output: bool,
                 workers: int):
        self._logger = logger
        self._sot_file = sot_file
        self._temp = temp
        self._base = base
        self._overlay = overlay
        self._modules = modules
        self._hydrated = hydrated
        self._output_subdir = output_subdir
        self._gatekeeper_validation = gatekeeper_validation
        self._gatekeeper_constraints = gatekeeper_constraints
        self._oci_registry = oci_registry
        self._oci_tags = oci_tags
        self._hyd_type = hydration_type
        self._preserve_temp = preserve_temp
        self._split_output = split_output
        self._workers = workers
        self._setup_validators()
        self._setup_oci_client()

    def _setup_validators(self):
        """Perform setup for validators

        Returns:
            sequence of validators to run
        """
        validators = []
        if self._gatekeeper_validation:
            try:
                validators.append(
                    Gatekeeper.configure(constraint_paths=self._gatekeeper_constraints))
            except ValueError as e:
                raise CliError(f'Error while setting up validators: {e}') from e

        self._validators = validators

    def _setup_oci_client(self):
        """Create OCI client if required

        Returns:
            client to interact with configured OCI registry
        """
        client = None
        if self._oci_registry:
            client = OCIClientFactory.create_client(
                registry_url=self._oci_registry)

        self._oci_client = client

    def report(self) -> dict[str, list[str]]:
        """Creates a pretty-printable summary of total hydration state

        Returns:
            results from `gather_failures`, dict `{item: [errors]}`
        """
        failures = self._gather_failures()
        if failures:
            total = len(self.hydrators)
            unsuccessful = len(failures.keys())
            successful = total - unsuccessful
            print(f'\nTotal {total} {self._hyd_type.value}s - {successful} rendered '
                  f'successfully, {unsuccessful} unsuccessful\n',
                  file=sys.stderr)
        else:
            print(f'{len(self.hydrators)} {self._hyd_type.value}s total, all rendered '
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
        for h in self.hydrators:
            if not h.success:
                if h.jinja_success is False:
                    failures[h.name].append('jinja')

                if h.kustomize_success is False:
                    failures[h.name].append('kustomize')

                if h.split_success is False:
                    failures[h.name].append('split output')

                if h.publish_success is False:
                    failures[h.name].append('OCI publish')

                if not h.validators_success:
                    for v in h.validated:
                        if v.valid is False:
                            failures[h.name].append(cap_word_to_snake_case(v.__class__.__name__))
        return failures

    async def _hydrate_one(self, config_data: SotConfig) -> None:
        """Workflow for hydrating just one item

        Args:
            config_data: item config as dict

        Returns:
            integer exit code
        """
        try:
            cfg: BaseConfig = config_data[self._name]
        except KeyError:
            self._logger.error(f'{self._name} not found in source of truth')
            return
        self._logger.info(f'Hydrating {self._name}')

        cls: type[ClusterHydrator] | type[PackageHydrator]
        if self._hyd_type is HydrateType.CLUSTER:
            cls = ClusterHydrator
        elif self._hyd_type is HydrateType.PACKAGE:
            cls = PackageHydrator
        else:
            raise CliError(f'Unknown hydration type {self._hyd_type}')

        hydrate = cls(
            config=cfg,
            temp=self._temp,
            base=self._base,
            overlay=self._overlay,
            modules=self._modules,
            hydrated=self._hydrated,
            output_subdir=self._output_subdir,
            oci_client=self._oci_client,
            oci_tags=self._oci_tags,
            validators=self._validators,
            preserve_temp=self._preserve_temp,
            split_output=self._split_output,
        )
        self.hydrators.append(hydrate)
        await hydrate.run()
        await hydrate.cleanup()

    def _filter_item(self, item: str, item_config: BaseConfig) -> bool:
        # if tags provided as args
        if self._tags:
            try:
                # split config tags into a set
                config_tags = {t.strip() for t in item_config.tags.split(",")}
            except KeyError:
                self._logger.warning(f'Config {item}: specified tags as args but has none in '
                                     f'config file')
                return True

            # if config tags don't intersect with tags from args,
            # then don't hydrate item
            if not config_tags.intersection(self._tags):
                self._logger.debug(f"Config {item}: no matching tags; not hydrating.")
                return True

        if self._group:
            if self._group.strip().lower() != item_config.group.strip().lower():
                self._logger.debug(f"{self._hyd_type.value.title()} '{item}': not in group "
                                   f"'{self._group}'; not hydrating...")
                return True

        return False

    def _generate_hydrators(self, cls, config_data):
        for c, cfg in config_data.items():
            self._logger.debug(f'Starting hydration setup for {c}')
            if self._filter_item(c, cfg):
                continue

            self._logger.info(f'Hydrating {c}')
            hydrator = cls(
                config=cfg,
                temp=TempDir(),
                base=self._base,
                overlay=self._overlay,
                modules=self._modules,
                hydrated=self._hydrated,
                output_subdir=self._output_subdir,
                oci_client=self._oci_client,
                oci_tags=self._oci_tags,
                validators=self._validators,
                preserve_temp=self._preserve_temp,
                split_output=self._split_output,
            )
            self.hydrators.append(hydrator)
            yield hydrator

    async def _hydration_worker(self, worker: int, queue: asyncio.Queue):
        self._logger.debug(f'Worker {worker} starting')
        while True:
            try:
                hydrator: BaseHydrator = queue.get_nowait()
            except asyncio.QueueEmpty:
                self._logger.debug(f'Worker {worker}: queue is empty; exiting.')
                return

            try:
                async with hydrator:
                    await hydrator.run()
            except Exception as e:  # pylint: disable=broad-exception-caught
                self._logger.exception(
                    f'Worker {worker} caught generic exception on hydrator {hydrator.name}',
                    exc_info=e)

    async def _hydrate_many_async(self, cls, config_data):
        tasks = []
        queue = asyncio.Queue()

        for hydrator in self._generate_hydrators(cls, config_data):
            queue.put_nowait(hydrator)

        for i in range(1, self._workers + 1):
            task = asyncio.create_task(self._hydration_worker(i, queue))
            tasks.append(task)

        await asyncio.gather(*tasks)

    # pylint: disable-next=too-many-branches
    async def _hydrate_many(self, config_data: SotConfig) -> None:
        """Workflow for hydrating more than one item, such as when a
        group, tags, or all items are scoped

        Args:
            config_data: item config as dict

        Returns:
            integer exit code
        """
        cls: type[ClusterHydrator] | type[PackageHydrator]
        if self._hyd_type is HydrateType.CLUSTER:
            cls = ClusterHydrator
        elif self._hyd_type is HydrateType.PACKAGE:
            cls = PackageHydrator
        else:
            raise CliError(f'Unknown hydration type {self._hyd_type}')

        # if we have more than zero workers
        if self._workers > 0:
            await self._hydrate_many_async(cls, config_data)
            return

        # otherwise...
        for hydrator in self._generate_hydrators(cls, config_data):
            async with hydrator:
                await hydrator.run()

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
        dcls = ClusterConfig if self._hyd_type is HydrateType.CLUSTER else PackageConfig
        with self._sot_file.open() as sot_f:
            self._logger.debug(f"Processing source of truth file: {sot_f.name}")
            reader = csv.DictReader(sot_f, dialect='excel')
            row: dict[str, str]
            for row in reader:
                cfg = dcls({k.strip(): v.strip() for k, v in row.items() if row})
                self._logger.debug(f'Got config from CSV:\n{pprint.pformat(cfg)}')

                try:
                    check_config(cfg)
                except ConfigWarning as e:
                    self._logger.warning(f"Skipping row {reader.line_num}: {e}")
                    del data[cfg.name]
                except ConfigError as e:
                    self._logger.error(e)
                    raise CliError(e) from e

                data[cfg.name] = cfg

        self._config = data
        return self._config

    async def run(self) -> int:
        """Runs the CLI workflow

        Returns:
            exit code as int
        """
        # get config from source-of-truth file
        try:
            config_data = self._process_sot_file()
        except CliError:
            return 1

        self.hydrators = []

        # a single name is specified
        if self._name:
            await self._hydrate_one(config_data)
        # filter by tags, groups, or process all in config
        else:
            await self._hydrate_many(config_data)

        failures = self.report()

        if failures:
            return 1
        return 0


# pylint: disable-next=too-many-instance-attributes,too-few-public-methods
class ClusterCli(BaseCli):
    """ CLI class for cluster hydration """

    def __init__(self, *,
                 cluster_name: str,
                 cluster_tag: set[str],
                 cluster_group: str,
                 **kwargs):
        super().__init__(**kwargs)
        self._name = cluster_name
        self._group = cluster_group
        self._tags = cluster_tag


class PackageCli(BaseCli):
    """ CLI class for package hydration """

    def __init__(self, *,
                 package_group: str,
                 package_tags: set[str],
                 **kwargs):
        super().__init__(**kwargs)
        self._name = package_group
        self._group = package_group
        self._tags = package_tags
