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
import pathlib
from typing import Any, Optional, Set

import aiofiles
import jinja2
import yaml

from .exc import CliError, CliWarning
from .krm import KrmResource, K8sResourceParser
from .oci_registry import OCIClient
from .process import Process
from .types import BaseConfig, HydrateType
from .util import is_jinja_template, \
    package_oci_artifact, TempDir, LoggingMixin, FileCache, InMemoryTextFile, \
    template_string
from .validator import BaseValidator


# pylint: disable-next=too-many-instance-attributes
class BaseHydrator(LoggingMixin):
    """ Implements a specific hydration flow """
    _jinja_env: jinja2.Environment
    _hydration_dest: pathlib.Path
    _oci_client: OCIClient
    _overlay_dir: pathlib.Path
    _visited_files: Set[pathlib.Path]

    __slots__ = [
        'config', '_logger', '_temp', '_base', '_overlay', '_modules', '_hydrated',
        '_output_subdir', '_oci_client', 'oci_tags', '_hyd_type', '_validators', 'validated',
        '_preserve_temp', '_split_output', 'success', 'split_success', 'jinja_success',
        'kustomize_success', 'validators_success', 'publish_success', '_jinja_env',
        '_hydration_dest', '_oci_client', '_overlay_dir', '_visited_files', 'rendered_fp'
    ]

    # pylint: disable-next=too-many-arguments
    def __init__(self, *,
                 config: BaseConfig,
                 temp: TempDir,
                 base: pathlib.Path,
                 overlay: pathlib.Path,
                 modules: pathlib.Path,
                 hydrated: pathlib.Path,
                 output_subdir: str,
                 oci_client: OCIClient,
                 oci_tags: Set[str],
                 hydration_type: HydrateType,
                 validators: list[BaseValidator] | None = None,
                 preserve_temp: bool = False,
                 split_output: bool = False):
        self.config = config
        # pylint: disable=duplicate-code
        self._temp = temp
        self._base = base
        self._overlay = overlay
        self._modules = modules
        self._hydrated = hydrated
        self._output_subdir = output_subdir
        self._oci_client = oci_client
        self.oci_tags = oci_tags
        self._hyd_type = hydration_type
        self._validators: list[BaseValidator] = validators if validators else []
        self.validated: list[BaseValidator] = []
        self._preserve_temp = preserve_temp
        self._split_output = split_output
        self.success: bool = True
        self.split_success: bool | None = None
        self.jinja_success: bool | None = None
        self.kustomize_success: bool | None = None
        self.validators_success: bool | None = None
        self.publish_success: bool | None = None
        self.rendered_fp: pathlib.Path | None = None

        self._setup_logger('hydrator', self.name)
        self._setup_jinja()

    async def __aenter__(self):
        pass

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.cleanup()

    @property
    def name(self):
        """Convenience property to grab configured name"""
        return self.config.name

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
        # set concrete overlay dir (which is the overlay folder + group name)
        self._overlay_dir = self._overlay.joinpath(self.config.group)
        if not self._overlay_dir.exists():
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

        self.jinja_success = True

        # setup hydrate source and destination directories and ensure dest exists
        hydration_src = self._temp.path.joinpath(self._overlay.name, self.config.group)
        if self._split_output:
            if self._hyd_type is HydrateType.CLUSTER:
                hydration_dest = self._hydrated.joinpath(self.config.group, self.name)
            elif self._hyd_type is HydrateType.PACKAGE:
                hydration_dest = self._hydrated.joinpath(self.name)
            else:
                raise CliError(f'Unknown hydration type {self._hyd_type}')
        else:
            if self._output_subdir == 'cluster':
                hydration_dest = self._hydrated.joinpath(self.name)
            elif self._output_subdir == 'group':
                hydration_dest = self._hydrated.joinpath(self.config.group)
            else:  # is none
                hydration_dest = self._hydrated

        hydration_dest.mkdir(parents=True, exist_ok=True)

        self._hydration_dest = hydration_dest

        # run kustomize in the temp dir for the cluster
        await self._run_kustomize(output_dir=hydration_dest,
                                  overlay_dir=hydration_src)

        if self._split_output and self.kustomize_success:
            await self._split_manifest(output_dir=hydration_dest)
        elif self._split_output and not self.kustomize_success:
            self.log('kustomize had errors; not splitting output', 'warning')

    def _generate_dirs(self):
        self.log(f'Traversing source base path: {self._base}', 'debug')
        yield from self._base.walk()

        self.log(f'Traversing source overlay path: {self._overlay_dir}', 'debug')
        yield from self._overlay_dir.walk()

        if not self._modules.exists() or not self._modules.is_dir():
            self.log(f'Modules directory path: {self._modules} is missing or invalid. '
                     f'Skipping traversal.', 'debug')
        else:
            self.log(f'Traversing source modules path: {self._modules}', 'debug')
            for root, dirs, files in self._modules.walk():
                if '.git' in dirs:
                    dirs.remove('.git')
                yield root, dirs, files

    def _prepare_dir(self, root):
        # compute relative path from src root to copy into dest
        try:
            # Check if the root is within the _modules directory
            if root == self._modules or self._modules in root.parents:
                # Construct the path to the base directory within self._temp
                base_dir_in_temp = self._temp.path.joinpath(
                    self._base.relative_to(self._base.parent))

                # Construct the path to the 'modules' directory within base_dir_in_temp
                relative_path = root.relative_to(self._modules)
                next_path = base_dir_in_temp.joinpath('modules', relative_path)
            else:
                relative_path = root.relative_to(self._base.parent)
                next_path = self._temp.path.joinpath(relative_path)
        except ValueError:
            relative_path = root.relative_to(self._overlay.parent)
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
        self.rendered_fp = output_dir.joinpath(filename).resolve()

        cmd = ["kustomize", "build", ".", "-o", str(self.rendered_fp)]

        p = Process(cmd, logger_name='kustomize', name=self.name)
        try:
            await p.run(cwd=overlay_dir)
        except CliWarning as e:
            self._set_failure(kustomize=True)
            raise CliError(str(e)) from e

        if p.proc.returncode == 0:  # type: ignore
            self.kustomize_success = True
        else:
            self._set_failure(kustomize=True)

    async def _open_or_create_file(self, file_path: pathlib.Path) -> Any:
        """Opens a file if it exists and hasn't been opened previously.
        Else, creates it and its parent directories.t

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
        """Splits a monolithic manifest into smaller manifests based on resource mappings.
           Updates 'self.rendered_fp' to new output directory containing all the split manifests.

            Args:
                output_dir: The output directory for the manifests.
                resource_to_path: Mapping of k8s resource keys to file paths.

            Raises:
                CliError: if any issues are encountered at runtime
        """
        base_library_dir_name = "base_library"
        assert isinstance(self.rendered_fp, pathlib.Path)
        krm_parser = K8sResourceParser()
        try:
            async with aiofiles.open(self.rendered_fp, 'r', encoding="utf-8") as f:
                self.log(f'Preparing to split manifest: {self.rendered_fp}', 'debug')
                yaml_docs = yaml.load_all(await f.read(), yaml.CSafeLoader)

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

                    s = yaml.dump(doc, Dumper=yaml.CSafeDumper, default_flow_style=False)
                    await f.write(s)
                    await f.close()
                else:
                    filtered_yaml_docs.append(doc)

            # overwrite output manifest with resources that could not be mapped back to templates
            if filtered_yaml_docs:
                async with aiofiles.open(self.rendered_fp, 'w', encoding="utf-8") as f:
                    await f.write(yaml.dump_all(filtered_yaml_docs, Dumper=yaml.CSafeDumper,
                                                default_flow_style=False))
            else:
                self.log(f'All resources moved to separate manifest files. Deleting original '
                         f'output manifest: {self.rendered_fp.name}.', 'debug')
                self.rendered_fp.unlink()
                self.rendered_fp = output_dir
        except FileNotFoundError as e:
            self._set_failure(split=True)
            raise CliError(f"Rendered manifest not found: {e}") from e

        except Exception as e:
            self._set_failure(split=True)
            raise CliError(f"Unexpected error: {e}") from e

    def _set_failure(self, jinja: bool = False, kustomize: bool = False, split: bool = False,
                     validator: bool = False, publish: bool = False):
        """
        Sets the failure flags for different stages of a process. Modifies the success attributes
        based on the boolean input parameters indicating failures in specific stages.

        Args:
            jinja (bool): Indicates whether there was a failure in the Jinja stage.
            kustomize (bool): Indicates whether there was a failure in the Kustomize stage.
            split (bool): Indicates whether there was a failure in the Split stage.
            validator (bool): Indicates whether there was a failure in the Validator stage.
            publish (bool): Indicates whether there was a failure in the Publish stage.
        """
        failure_modes = (jinja, kustomize, split, validator, publish)
        if any(failure_modes):
            self.success = False
        if jinja:
            self.jinja_success = False
        if kustomize:
            self.kustomize_success = False
        if split:
            self.split_success = False
        if validator:
            self.validators_success = False
        if publish:
            self.publish_success = False

    async def _validate(self):
        """Runs provided validators. Should be run after templates are hydrated
        """
        if self.rendered_fp is None:
            self.log('No rendered manifest to validate.', 'warning')
            return

        any_failed = False
        for validator in self._validators:
            v = validator(self.name)
            try:
                await v.run(config=self.config, hydrated_path=self.rendered_fp)
            except CliWarning:
                pass

            self.validated.append(v)

            if not v.valid:
                any_failed = True

        if any_failed:
            self._set_failure(validator=True)
            self.log("One or more validators failed. See logs for details.", 'warning')
        else:
            self.validators_success = True

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

        self.publish_success = True

    async def cleanup(self) -> None:
        """Removes the directories/files created by hydration, specifically
        the temp space.
        """
        if not self._preserve_temp:
            await self._temp.cleanup()

    async def run(self) -> None:
        """Orchestrates the hydration process flow.
        """
        self.log(f'Using temp dir: {self._temp.path}', 'debug')
        self._visited_files = set()
        await self._hydrate()

        if (self.jinja_success is not False
                and self.kustomize_success is not False
                and self.split_success is not False):
            await self._validate()
        else:
            errs = []
            if self.jinja_success is False:
                errs.append('Jinja')
            if self.kustomize_success is False:
                errs.append('Kustomize')
            if self.split_success is False:
                errs.append('split output')
            self.log(f"not validating due to issues with {", ".join(errs)}", 'warning')

        if self.success and self._oci_client:
            try:
                self._publish()
            except CliWarning:
                self._set_failure(publish=True)
                self.log("One or more publishers failed. See "
                         "logs for details.", 'warning')
                self.log("could not publish to oci registry", 'warn')


class ClusterHydrator(BaseHydrator):
    """ Hydrator class for cluster-specific resources """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, hydration_type=HydrateType.CLUSTER, **kwargs)
        _name_key = 'cluster_name'
        _group_key = 'cluster_group'
        _tags_key = 'cluster_tags'
        _disp_name = 'cluster'


class PackageHydrator(BaseHydrator):
    """ Hydrator class for packages """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, hydration_type=HydrateType.PACKAGE, **kwargs)
        self._name_key = 'group'
        self._group_key = 'group'
        self._tags_key = 'tags'
        self._disp_name = 'group'
