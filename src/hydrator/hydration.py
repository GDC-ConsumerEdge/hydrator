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
import pathlib
from typing import Any, Optional, Set

import aiofiles
import jinja2
import yaml

from .exc import CliError, CliWarning
from .krm import KrmResource, K8sResourceParser
# OCIClient is part of HydratorSharedConfig, no direct import needed here if not used otherwise
from .process import Process
from .types import BaseConfig, HydrateType, HydratorStatus, HydratorSharedConfig # Added HydratorSharedConfig
from .util import is_jinja_template, package_oci_artifact, TemporaryDirectory, LoggingMixin, \
    FileCache, InMemoryTextFile, template_string, sync_load_all_yaml
# BaseValidator is part of HydratorSharedConfig


# pylint: disable-next=too-many-instance-attributes
class BaseHydrator(LoggingMixin):
    """ Implements a specific hydration flow """
    _jinja_env: jinja2.Environment
    _hydration_dest: pathlib.Path
    # _oci_client is now part of shared_config
    _visited_files: Set[pathlib.Path]

    # Updated __slots__
    __slots__ = [
        'config', '_logger', '_temp', '_base_root_path', '_overlay_root_path', '_modules_path',
        '_hydrated_path', '_output_subdir', '_oci_client', 'oci_tags', '_hyd_type', '_validators',
        'validated', '_preserve_temp', '_split_output', 'status', '_jinja_env', '_hydration_dest',
        '_overlay_path', '_visited_files', 'rendered_path'
        # Removed duplicate _oci_client from original slots, ensured all shared_config items are covered
    ]

    def __init__(self, *,
                 item_config: BaseConfig,
                 shared_config: HydratorSharedConfig,
                 temp: TemporaryDirectory,
                 hydration_type: HydrateType):
        if self.__class__ is BaseHydrator:
            raise TypeError("BaseHydrator cannot be directly instantiated")

        # Ensure self.config is set before logger setup uses self.name
        self.config = item_config
        self._temp = temp

        # Attributes from shared_config
        self._base_root_path = shared_config.base_path
        self._overlay_root_path = shared_config.overlay_path # Root for overlays
        self._modules_path = shared_config.modules_path
        self._hydrated_path = shared_config.hydrated_path
        self._output_subdir = shared_config.output_subdir
        self._oci_client = shared_config.oci_client # Optional OCIClient
        self.oci_tags = shared_config.oci_tags # Optional Set[str], public
        self._validators: list[BaseValidator] = shared_config.validators
        self._preserve_temp = shared_config.preserve_temp
        self._split_output = shared_config.split_output
        
        self._hyd_type = hydration_type # Store hydration_type

        # Construct the specific overlay path for this item
        _overlay = self._overlay_root_path / item_config.group
        _default_overlay_path = self._overlay_root_path / shared_config.default_overlay if shared_config.default_overlay else None
        self._overlay_path: pathlib.Path | None
        if _overlay.exists():
            self._overlay_path = _overlay
        elif _default_overlay_path and _default_overlay_path.exists():
            self._overlay_path = _default_overlay_path
        else:
            self._overlay_path = None
            self.log(f"Warning: No overlay found for group '{item_config.group}' and no default overlay applicable.", 'warning')


        # Public attributes
        self.validated: list[BaseValidator] = [] # Initialize as empty list
        self.status = HydratorStatus()
        self.rendered_path: pathlib.Path | None = None

        self._setup_logger('hydrator', self.name)
        self._setup_jinja()

    async def __aenter__(self):
        return await self._temp.__aenter__()

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return await self._temp.__aexit__(exc_type, exc_val, exc_tb)

    @property
    def name(self):
        """Convenience property to grab configured name"""
        return self.config.name # Uses item_config via self.config

    def _setup_jinja(self) -> None:
        """Setup jinja environment using internal tempdir at `self._temp`
        and sets at `self._jinja_env`
        """
        self.log(f'Setting up Jinja with template loader path: {self._temp.path}', 'debug')
        self._jinja_env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(self._temp.path),
            autoescape=True,
            trim_blocks=True,
            lstrip_blocks=True,
            enable_async=True)

    # pylint: disable-next=too-many-branches
    async def _hydrate(self) -> None:
        """Process a given item using internal `config` """
        if self._overlay_path is None:
            self.log(f"missing overlay for group '{self.config.group}'; "
                     f"nothing to hydrate", 'warning')
            raise CliWarning('No overlay')

        # walk the sources (base and overlay), copy to temp dir, Jinja-template as needed
        try:
            if self._split_output:
                krm_parser = K8sResourceParser()
                await self._process_dirs(krm_parser)
            else:
                await self._process_dirs()
        except CliWarning as e:
            if isinstance(e.__cause__, jinja2.exceptions.TemplateError):
                self._set_failure(jinja=True)
                self.log('Jinja template errors', 'warning')
            elif isinstance(e.__cause__, yaml.YAMLError):
                self._set_failure(split=True)
            # we can't proceed if copying/templating had issues
            self.log(f'Not proceeding due to errors during copy/templating: {e}', 'error')
            return

        # setup hydrate source and destination directories and ensure dest exists
        hydration_src = self._temp.path.joinpath(*self._overlay_path.parts[-2:])
        if self._split_output:
            if self._hyd_type is HydrateType.CLUSTER:
                hydration_dest = self._hydrated_path.joinpath(self.config.group, self.name)
            elif self._hyd_type is HydrateType.GROUP:
                hydration_dest = self._hydrated_path.joinpath(self.name)
            else:
                raise CliError(f'Unknown hydration type {self._hyd_type}')
        else:
            if self._output_subdir == 'cluster':
                hydration_dest = self._hydrated_path.joinpath(self.name)
            elif self._output_subdir == 'group':
                hydration_dest = self._hydrated_path.joinpath(self.config.group)
            else:  # is none
                hydration_dest = self._hydrated_path

        hydration_dest.mkdir(parents=True, exist_ok=True)

        self._hydration_dest = hydration_dest

        # run kustomize in the temp dir for the cluster
        await self._run_kustomize(output_dir=hydration_dest,
                                  overlay_dir=hydration_src)

        if self._split_output and self.status.kustomize_ok:
            await self._split_manifest(output_dir=hydration_dest)
        elif self._split_output and not self.status.kustomize_ok:
            self.log('kustomize had errors; not splitting output', 'warning')

    def _generate_dirs(self):
        self.log(f'Traversing source base path: {self._base_root_path}', 'debug')
        yield from self._base_root_path.walk()

        self.log(f'Traversing source overlay path: {self._overlay_path}', 'debug')
        yield from self._overlay_path.walk()

        if not self._modules_path.exists() or not self._modules_path.is_dir():
            self.log(f'Modules directory path: {self._modules_path} is missing or invalid. '
                     f'Skipping traversal.', 'debug')
        else:
            self.log(f'Traversing source modules path: {self._modules_path}', 'debug')
            for root, dirs, files in self._modules_path.walk():
                if '.git' in dirs:
                    dirs.remove('.git')
                yield root, dirs, files

    def _prepare_dir(self, root):
        # compute relative path from src root to copy into dest
        try:
            # Check if the root is within the _modules directory
            if root == self._modules_path or self._modules_path in root.parents:
                # Construct the path to the base directory within self._temp
                base_dir_in_temp = self._temp.path.joinpath(
                    self._base_root_path.relative_to(self._base_root_path.parent))

                # Construct the path to the 'modules' directory within base_dir_in_temp
                relative_path = root.relative_to(self._modules_path)
                next_path = base_dir_in_temp.joinpath('modules', relative_path)
            else:
                relative_path = root.relative_to(self._base_root_path.parent)
                next_path = self._temp.path.joinpath(relative_path)
        except ValueError:
            relative_path = root.relative_to(self._overlay_root_path.parent)
            next_path = self._temp.path.joinpath(relative_path)

        return next_path, relative_path

    # pylint: disable-next=too-many-locals
    async def _process_dirs(self, krm_parser: Optional[K8sResourceParser] = None) -> None:
        """ Walks the base and overlay directories loading files contained within.  Files are
        loaded into memory, Jinja templated, and written to the temp directory.  If split output
        is enabled (`krm_parser` is not None), the KrmResourceParser is used to process YAML
        strings for use during split output.

        Args:
            krm_parser: Optional K8sResourceParser for processing YAML strings during split output.
        """

        file_cache = FileCache()

        for (root, _, files) in self._generate_dirs():
            dest_dir, relative_dir = self._prepare_dir(root)

            if not dest_dir.exists():
                self.log(f"Creating directory: {dest_dir}", 'debug')
                dest_dir.mkdir(parents=True, exist_ok=True)

            for f in files:
                src_f = root.joinpath(f)
                dst_f = dest_dir.joinpath(f)
                dst_f_no_j2 = dst_f.with_suffix("") if is_jinja_template(dst_f) else dst_f

                self.log(f'Templating {src_f} and writing to {dst_f_no_j2}', 'debug')

                # load file into file_cache. file_cache_key should be a relative path in the
                # templates dirs, not a full path into the temp dirs
                file_cache_key = relative_dir.joinpath(f)
                if file_cache_key not in file_cache:
                    file_cache[file_cache_key] = await InMemoryTextFile.from_file(src_f)
                in_mem_file = file_cache[file_cache_key]

                templated_str = None
                processed_str = None
                rel_dest_no_j2 = None
                if is_jinja_template(dst_f):
                    rel_dest_no_j2 = file_cache_key.with_suffix("")
                    templated_str = await template_string(str(in_mem_file),
                                                          cluster_config=self.config,
                                                          hydrator=self)

                # if we have a krm_parser, we're doing split output
                if krm_parser:
                    processed_str = await krm_parser.process_yaml_string(
                        templated_str if templated_str else str(in_mem_file),
                        path=pathlib.Path(rel_dest_no_j2) if rel_dest_no_j2 else file_cache_key,
                        unique_id=self.name
                    )

                if processed_str:
                    content_to_write = processed_str
                elif templated_str:
                    content_to_write = templated_str
                else:
                    content_to_write = str(in_mem_file)

                async with aiofiles.open(dst_f_no_j2, 'w', encoding="utf-8") as f:
                    await f.write(content_to_write)

        self.log('Done processing source packages', 'debug')

    async def _run_kustomize(self, *, output_dir: pathlib.Path,
                             overlay_dir: pathlib.Path) -> None:
        """Runs kustomize using `subprocess.Popen` and dumps the output to a
        specified location.  Notably stderr and stdout are piped to different
        buffers and as such logs may appear out of order.

        Args:
            output_dir: directory to write the kustomize-generated template
            overlay_dir: directory where overlay has been copied and
                templated (becomes kustomize cwd)

        Raises:
            CliError: if any issues are encountered at runtime; other
                exceptions are unexpected
        """
        filename = f'{self.name}.yaml'
        self.rendered_path = output_dir.joinpath(filename).resolve()

        cmd = ["kustomize", "build", ".", "-o", str(self.rendered_path)]

        p = Process(cmd, logger_name='kustomize', name=self.name)
        try:
            await p.run(cwd=overlay_dir)
        except CliWarning as e:
            self._set_failure(kustomize=True)
            raise CliError(str(e)) from e

        if p.proc.returncode != 0:  # type: ignore
            self._set_failure(kustomize=True)

    async def _open_or_create_file(self, file_path: pathlib.Path) -> Any:
        """ Opens a file if it exists and hasn't been opened previously; otherwise creates it and
        its parent directories.

        Args:
            file_path: The path to the file.

        Returns:
            An open file object.
        """
        try:
            if file_path in self._visited_files:
                # we call __aenter__ because we are not using the async context manager
                # pylint: disable=unnecessary-dunder-call
                f = await aiofiles.open(file_path, 'a+', encoding="utf-8").__aenter__()
                await f.write('---\n')
                return f

            self._visited_files.add(file_path)
            # we call __aenter__ because we are not using the async context manager
            # pylint: disable=unnecessary-dunder-call
            return await aiofiles.open(file_path, 'w', encoding="utf-8").__aenter__()
        except FileNotFoundError:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            # we call __aenter__ because we are not using the async context manager
            # pylint: disable=unnecessary-dunder-call
            return await aiofiles.open(file_path, 'w', encoding="utf-8").__aenter__()

    async def _split_manifest(self, *, output_dir: pathlib.Path) -> None:
        """ Splits a monolithic manifest into smaller manifests based on resource mappings.
           Updates 'self.rendered_fp' to new output directory containing all the split manifests.

        Args:
            output_dir: The output directory for the manifests.
            resource_to_path: Mapping of k8s resource keys to file paths.

        Raises:
            CliError: if any issues are encountered at runtime
        """
        base_library_dir_name = "base_library"
        assert isinstance(self.rendered_path, pathlib.Path)
        krm_parser = K8sResourceParser()
        try:
            async with aiofiles.open(self.rendered_path, 'r', encoding="utf-8") as f:
                self.log(f'Preparing to split manifest: {self.rendered_path}', 'debug')
                content = await f.read()
                yaml_docs = await asyncio.to_thread(sync_load_all_yaml, content)

            filtered_yaml_docs: list[dict[str, Any]] = []
            for doc in map(KrmResource, yaml_docs):
                output_file = krm_parser.get_path(doc, unique_id=self.name)

                if output_file:
                    output_file = output_dir.joinpath(output_file)

                    # remove the base_library subdirectory from file path
                    if base_library_dir_name in output_file.parts:
                        output_file = pathlib.Path(*(part for part in output_file.parts
                                                     if part != base_library_dir_name))

                    f = await self._open_or_create_file(output_file)
                    self.log(
                        f'{"Appending" if f.mode == "a+" else "Writing"} resource '
                        f'"{doc.kind}" with name "{doc.name if doc.name else "no name"}" in '
                        f'namespace "{doc.namespace if doc.namespace else "default"}" to: '
                        f'{output_file}', 'debug')

                    try:
                        del doc['metadata']['annotations'][K8sResourceParser.annotation]
                    except KeyError:
                        self.log('Encountered error removing UID; this is unexpected but '
                                 'probably innocuous', 'info')

                    # Ensure doc is a dict before dumping
                    doc_to_dump = doc if isinstance(doc, dict) else doc.data
                    yaml_string: str = await asyncio.to_thread(  # type: ignore
                        yaml.dump,
                        doc_to_dump,
                        Dumper=yaml.CSafeDumper,
                        default_flow_style=False)
                    await f.write(yaml_string)
                    await f.close()
                else:
                    filtered_yaml_docs.append(doc)

            # overwrite output manifest with resources that could not be mapped back to templates
            if filtered_yaml_docs:
                # Ensure all items in filtered_yaml_docs are dicts
                docs_to_dump = [d if isinstance(d, dict) else d.data for d in filtered_yaml_docs]
                yaml_string = await asyncio.to_thread(  # type: ignore
                    yaml.dump_all,
                    docs_to_dump,
                    Dumper=yaml.CSafeDumper,
                    default_flow_style=False)
                async with aiofiles.open(self.rendered_path, 'w', encoding="utf-8") as f:
                    await f.write(yaml_string)
            else:
                self.log(f'All resources moved to separate manifest files. Deleting original '
                         f'output manifest: {self.rendered_path.name}.', 'debug')
                self.rendered_path.unlink()
                self.rendered_path = output_dir
        except FileNotFoundError as e:
            self._set_failure(split=True)
            raise CliError(f"Rendered manifest not found: {e}") from e

        except Exception as e:
            self._set_failure(split=True)
            raise CliError(f"Unexpected error: {e}") from e

    def _set_failure(self,
                     hydrator: bool = False,
                     jinja: bool = False,
                     kustomize: bool = False,
                     split: bool = False,
                     validator: bool = False,
                     publish: bool = False):
        """
        Sets the failure flags for different stages of a process. Modifies the success attributes
        based on the boolean input parameters indicating failures in specific stages.

        Args:
            hydrator (bool): Indicates whether there was a failure in hydrator
            jinja (bool): Indicates whether there was a failure in the Jinja stage.
            kustomize (bool): Indicates whether there was a failure in the Kustomize stage.
            split (bool): Indicates whether there was a failure in the Split stage.
            validator (bool): Indicates whether there was a failure in the Validator stage.
            publish (bool): Indicates whether there was a failure in the Publish stage.
        """
        if hydrator:
            self.status.hydrator_ok = False
        if jinja:
            self.status.jinja_ok = False
        if kustomize:
            self.status.kustomize_ok = False
        if split:
            self.status.split_ok = False
        if validator:
            self.status.validators_ok = False
        if publish:
            self.status.publish_ok = False

    async def _validate(self):
        """Runs provided validators. Should be run after templates are hydrated
        """
        if self.rendered_path is None:
            self.log('No rendered manifest to validate.', 'warning')
            return

        any_failed = False
        for validator in self._validators:
            v = validator(self.name)
            try:
                await v.run(config=self.config, hydrated_path=self.rendered_path)
            except CliWarning:
                pass

            self.validated.append(v)

            if not v.valid:
                any_failed = True

        if any_failed:
            self._set_failure(validator=True)
            self.log("One or more validators failed. See logs for details.", 'warning')

    def _publish(self) -> None:
        """Publishes to OCI registry. Should be run after optional validation
        of hydrated templates
        """
        hydrated_manifest_path = self._hydration_dest.joinpath(
            f"{self.name}.yaml")
        packaged_manifest_path = self._temp.path.joinpath(
            f"{self.name}.tar.gz")

        package_oci_artifact(
            hydrated_manifest_path,
            packaged_manifest_path,
            self._logger,
        )

        self._oci_client.push(
            packaged_manifest_path,
            self.name,
            self.oci_tags)

    async def run(self) -> None:
        """Orchestrates the hydration process flow.
        """
        self.log(f'Using temp dir: {self._temp.path}', 'debug')
        self._visited_files = set()
        try:
            await self._hydrate()
        except CliWarning:
            self._set_failure(hydrator=True)
            return

        if self.status.jinja_ok and self.status.kustomize_ok and self.status.split_ok:
            await self._validate()
        else:
            errs = []
            if self.status.jinja_ok is False:
                errs.append('Jinja')
            if self.status.kustomize_ok is False:
                errs.append('Kustomize')
            if self.status.split_ok:
                errs.append('split output')
            self.log(f"not validating due to issues with {", ".join(errs)}", 'warning')

        if self.status and self._oci_client:
            try:
                self._publish()
            except CliWarning:
                self._set_failure(publish=True)
                self.log("One or more publishers failed. See "
                         "logs for details.", 'warning')
                self.log("could not publish to oci registry", 'warn')


class Hydrator(BaseHydrator):
    """ Unified hydrator class for processing items based on HydrateType. """

    # __slots__ could be defined here if Hydrator adds more attributes,
    # but for now, it uses BaseHydrator's slots.

    def __init__(self, *,
                 item_config: BaseConfig,
                 shared_config: HydratorSharedConfig,
                 temp: TemporaryDirectory,
                 hydration_type: HydrateType):
        super().__init__(item_config=item_config,
                         shared_config=shared_config,
                         temp=temp,
                         hydration_type=hydration_type)
        # self._shared_config = shared_config # Already handled by BaseHydrator
        # self._hydration_type = hydration_type # Already handled by BaseHydrator

        # _disp_name is not explicitly set as an attribute.
        # Logging can use self._hyd_type.value directly if needed, e.g.:
        # self.log(f"Processing {self._hyd_type.value} {self.name}")
        # The item's specific name (self.name) is used for logger setup in BaseHydrator.
        # Keys like name_field, group_field, tags_field are accessible via self.config.
