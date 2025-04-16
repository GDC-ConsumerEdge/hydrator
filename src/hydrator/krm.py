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
import json
import pathlib
from collections import defaultdict
from typing import Any

import yaml

from .exc import CliWarning
from .util import SingletonMixin, is_valid_object, sha256_digest, LoggingMixin, sync_load_all_yaml

type KrmObjectKey = str
type YamlDoc = dict[str, Any]


class KrmResource(dict):
    # pylint: disable=missing-function-docstring
    """ Dict for KRM objects with customconvenience properties
    """

    @property
    def name(self):
        return self.get('metadata', {}).get('name', None)

    @property
    def namespace(self):
        return self.get('metadata', {}).get('namespace', None)

    @property
    def kind(self):
        return self.get('kind', None)


def represent_krm_resource(dumper: yaml.SafeDumper, data: KrmResource):
    """ Custom YAML representer for KrmResource objects
    """
    return dumper.represent_dict(data.items())


yaml.add_representer(KrmResource, represent_krm_resource, Dumper=yaml.CSafeDumper)  # type: ignore


def krm_add_annotation(obj: dict, *, key: str, value: str) -> dict:
    """ Adds annotation to KRM object
    """
    metadata = obj.setdefault('metadata', {})
    annotations = metadata.setdefault('annotations', {})
    annotations[key] = value
    return obj


class K8sResourceParser(SingletonMixin, LoggingMixin):
    """Parses Kubernetes resource definition files and creates a mapping between
    resource keys and file paths."""
    annotation = "hydrator-uid"

    def __init__(self) -> None:
        if not self._initialized:
            self._setup_logger('krm-parser')
            self._uid_to_path: defaultdict[str, set[pathlib.Path]] = defaultdict(set)
            self._overlay_resources: dict[str, list[pathlib.Path]] = {}
            self._initialized = True

    @staticmethod
    def _generate_key(yaml_doc: YamlDoc) -> KrmObjectKey:
        """Generate key to uniquely identify a k8s resource definition

        Args:
        yaml_doc (dict[str, Any]): The YAML document representing the resource.

        Returns:
            KrmObjectKey: A unique key for the resource.

        Raises:
            CliError: If the YAML document is invalid or missing required fields.
        """
        if not is_valid_object(yaml_doc):
            raise CliWarning('Invalid k8s resource definition: is not a valid resource')

        uid = (yaml_doc.get('metadata', {})
               .get('annotations', {})
               .get(K8sResourceParser.annotation, None))

        return uid or sha256_digest(json.dumps(yaml_doc, sort_keys=True))

    @staticmethod
    def _generate_path_key(resource_key: str, unique_id: str):
        uid = f'uid:{unique_id},resource_key:{resource_key}'
        path_key = sha256_digest(uid)
        return path_key

    def _in_overlay_paths(self, path, unique_id) -> bool:
        path_parts = path.parent.parts
        for o in self._overlay_resources[unique_id]:
            overlay_parts = o.parts
            try:
                idx = overlay_parts.index(path_parts[0])
            except ValueError:
                continue

            if path_parts == overlay_parts[idx:]:
                return True
        return False

    def get_path(self, doc, /, unique_id) -> pathlib.Path | None:
        """
        Retrieve the path associated with a unique ID and document.

        This function generates a resource key based on the specified document and then
        generates a path key using this resource key and the provided unique ID. The path
        associated with the generated path key is then retrieved from an internal mapping.

        Args:
            doc (str): The document for which to generate the resource key.
            unique_id (str): The unique identifier used to generate the path key.

        Returns:
            str: The path associated with the given document and unique ID.
        """
        resource_key = self._generate_key(doc)
        path_key = self._generate_path_key(resource_key=resource_key, unique_id=unique_id)

        if len(self._uid_to_path[path_key]) == 1:
            return next(iter(self._uid_to_path[path_key]))

        for path in self._uid_to_path[path_key]:
            if self._in_overlay_paths(path, unique_id):
                return path

        return None

    async def process_yaml_string(self, yaml_string: str,
                                  /, path: pathlib.Path, unique_id: str) -> str:
        """Parses a Kubernetes resource definition string and creates a mapping between
        the generated resource key and the context path(s) it is seen in.

        Args:
            yaml_string (str): The YAML string containing Kubernetes resource definitions.
            path  (pathlib.Path): The `yaml_string` file (context)
            unique_id (str): The unique identifier associated with zcontext, like cluster
              or group name


        Returns:
            str: The processed YAML documents.

        Raises:
            CliWarning: If the YAML string is empty or invalid.
        """
        processed_docs = []
        try:
            yaml_docs = await asyncio.to_thread(sync_load_all_yaml, yaml_string)
            for doc in yaml_docs:
                try:
                    assert isinstance(doc, dict)
                    resource_key = self._generate_key(doc)
                except CliWarning as e:
                    self.log(
                        f'Ignoring YAML doc; this warning is expected for '
                        f'non-k8s YAML files. Message: {e}', 'warning')
                    continue
                except AssertionError:
                    continue

                # extract resources in the overlay's kustomization file. it is assumed that:
                # 1) there is a single kustomization file for each group
                # 2) parse_resource will parse exactly one overlay kustomize file per hydration
                if doc["kind"] == "Kustomization":
                    if any(parent.name == "overlays" for parent in path.parents):
                        self._overlay_resources[unique_id] = [
                            path.parent.joinpath(resource).resolve() for resource in
                            doc.get('resources', [])]
                    continue

                krm_add_annotation(doc, key=K8sResourceParser.annotation, value=resource_key)
                processed_docs.append(doc)

                uid_path_key = self._generate_path_key(resource_key, unique_id)
                self._uid_to_path[uid_path_key].add(path)


        except yaml.YAMLError as e:
            msg = f"Error parsing YAML: {e}"
            raise CliWarning(msg) from e
        except AttributeError as e:
            msg = f"Error processing YAML document: {e}"
            raise CliWarning(msg) from e

        yaml_string = await asyncio.to_thread(
            yaml.dump_all,
            processed_docs,
            Dumper=yaml.CSafeDumper,
            explicit_start=True)
        return yaml_string
