# -*- coding: utf-8 -*-
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
import abc
import enum


class HydrateType(enum.Enum):
    """ Represents two current types of hydration """
    CLUSTER = 'cluster'
    GROUP = 'group'


class BaseConfig(abc.ABC, dict):
    """ Base class for item configuration; not expected to be instantiated directly """
    name_field: str
    group_field: str
    tags_field: str

    def __setattr__(self, key, value):
        raise AttributeError(f'Cannot assign attribute "{key}"')

    @property
    def name(self) -> str:
        """ Name uniform property getter """
        try:
            return self[self.__class__.name_field]
        except KeyError as e:
            raise AttributeError('Attribute _name_field not set') from e

    @property
    def group(self) -> str:
        """ Group uniform property getter """
        try:
            return self[self.__class__.group_field]
        except KeyError as e:
            raise AttributeError('Attribute _group_field not set') from e

    @property
    def tags(self) -> str:
        """" Tags uniform property getter """
        try:
            return self[self.__class__.tags_field]
        except KeyError as e:
            raise AttributeError('Attribute _tags_field not set') from e


class ClusterConfig(BaseConfig):
    """ Represents a single cluster configuration parsed from SoT """
    name_field = "cluster_name"
    group_field = "cluster_group"
    tags_field = "cluster_tags"


class GroupConfig(BaseConfig):
    """ Represents a single package configuration parsed from SoT """
    name_field = "group"
    group_field = "group"
    tags_field = "tags"


type SotConfig = dict[str, BaseConfig]
