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
# pylint: disable=unnecessary-pass
class CliWarning(Warning):
    """For use when a non-fatal error is encountered in the CLI flow,
    where it's desirable to alert to or cope with the current issue but not
    necessary to terminate execution.  If the program should terminate,
    don't use this exception.
    """
    pass


class CliError(Exception):
    """For use when a fatal error is encountered in the CLI flow, where it's
    necessary to alert to or cope with the current error/exception and/or is
    necessary to terminate execution
    """
    pass


class ConfigWarning(Warning):
    """ For use when a non-fatal error is encountered in parsing/validating configuration that
    should not cause program flow to terminate or exit immediately
    """
    pass


class ConfigError(Exception):
    """ For use when a fatal error is encountered in parsing/validating configuration that
    should cause program flow to terminate or exit immediately with a non-zero exit code
    """
    pass
