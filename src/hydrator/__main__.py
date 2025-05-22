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
import pprint
import sys

from .cli import parse_args, ClusterCli, GroupCli
from .exc import CliError
from .types import HydrateType, CliConfig, CliPathsConfig, CliOciConfig, CliValidationConfig, CliBehaviorConfig # Updated imports
from .util import setup_logger


def main() -> int:
    """Main CLI entry point
    """
    try:
        args = parse_args()
    except CliError:
        sys.exit(1)

    if args.quiet:
        logger = setup_logger(name='cli', level='ERROR')
    elif args.verbose == 1:
        logger = setup_logger(name='cli', level='INFO')
    elif args.verbose >= 2:
        logger = setup_logger(name='cli', level='DEBUG')
    else:
        logger = setup_logger(name='cli')

    logger.debug(f'Received args: {pprint.pformat(vars(args))}')

    try:
        # pylint: disable-next=protected-access,line-too-long
        logger.debug(
            f"Running with GIL {'enabled' if sys._is_gil_enabled() else 'disabled'} "
            f"on Python {sys.version}"  # type: ignore
        )
    except AttributeError:
        logger.debug(f'Running with GIL enabled on Python {sys.version}')

    cli: GroupCli | ClusterCli

    # Create CliConfig from args using the new nested structure
    paths_config = CliPathsConfig(
        sot_file=args.sot_file,
        temp_path=args.temp,
        base_path=args.base,
        overlay_path=args.overlay,
        modules_path=args.modules,
        hydrated_path=args.hydrated
    )

    oci_config = CliOciConfig(
        registry=args.oci_registry,
        tags=args.oci_tags
    )

    validation_config = CliValidationConfig(
        gatekeeper_validation=args.gatekeeper_validation,
        gatekeeper_constraints=args.gatekeeper_constraints
    )

    behavior_config = CliBehaviorConfig(
        output_subdir=args.output_subdir,
        preserve_temp=args.preserve_temp,
        split_output=args.split_output,
        workers=args.workers,
        default_overlay=args.default_overlay # Moved here
    )

    cli_config = CliConfig(
        paths=paths_config,
        oci=oci_config,
        validation=validation_config,
        behavior=behavior_config,
        hydration_type=args.type
    )

    try:
        if args.type is HydrateType.CLUSTER:
            cli = ClusterCli(
                logger=logger,
                config=cli_config,
                cluster_name=args.cluster_name,
                cluster_tag=args.cluster_tag,
                cluster_group=args.cluster_group
            )
        elif args.type is HydrateType.GROUP:
            cli = GroupCli(
                logger=logger,
                config=cli_config,
                groups=args.group,
                tags=args.tag
            )
    except CliError as e:
        logger.error(e)
        return 1

    return asyncio.run(cli.run())


if __name__ == '__main__':
    sys.exit(main())
