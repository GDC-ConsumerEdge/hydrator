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
import csv
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest
from dataclasses import dataclass
from typing import Sequence

import yaml


@dataclass
class ExecResult:
    proc: subprocess.CompletedProcess
    out: pathlib.Path
    temp: tempfile.TemporaryDirectory


def run_cli(sot: str,
            verbosity_arg: str = '-v',
            main_args: Sequence[str] = None,
            subcommand_args: Sequence = None,
            print_command: bool = False) -> ExecResult:
    proc = shutil.which('hydrate')
    main_args = main_args or []
    subcommand_args = subcommand_args or []
    tmpdir = tempfile.TemporaryDirectory()
    temp_args = ['-y', tmpdir.name]

    args = [proc, verbosity_arg, *main_args, 'cluster', *temp_args, *subcommand_args, sot]
    if print_command:
        print("\n" + " ".join(args))
    p = subprocess.run(args, capture_output=True, text=True)
    return ExecResult(proc=p, out=pathlib.Path(tmpdir.name + "/"), temp=tmpdir)


class TestClusterHydrationPlatformValidCases(unittest.TestCase):
    standard_args = [
        '-b', 'tests/assets/platform_valid/base_library',
        '-o', 'tests/assets/platform_valid/overlays',
    ]

    def basic_checks(self, results):
        self.assertEqual(0, results.proc.returncode)
        self.assertIn("4 clusters total, all rendered successfully",
                      results.proc.stdout)
        self.assertEqual("", results.proc.stderr)
        with open('tests/assets/platform_valid/sot.csv') as f:
            reader = csv.reader(f)
            next(reader)  # discard header
            for name, group, _, _ in reader:
                output_file = results.out.joinpath(f'{group}/{name}.yaml')
                self.assertTrue(output_file.exists()),
                self.assertTrue(output_file.is_file())
        results.temp.cleanup()

    def test_standard_args(self):
        r = run_cli('tests/assets/platform_valid/sot.csv',
                    subcommand_args=self.standard_args)
        self.basic_checks(r)

    def test_standard_args_verbosity_one(self):
        r = run_cli('tests/assets/platform_valid/sot.csv', subcommand_args=self.standard_args)

        info = re.findall(re.compile(r"^\w+\s+INFO.*", re.MULTILINE),
                          r.proc.stdout)
        self.assertGreater(len(info), 0)

        debug = re.findall(re.compile(r"^\w+\s+DEBUG.*", re.MULTILINE),
                           r.proc.stdout)
        self.assertEqual(0, len(debug))

        self.basic_checks(r)

    def test_standard_args_verbosity_two(self):
        r = run_cli('tests/assets/platform_valid/sot.csv',
                    verbosity_arg='-vv', subcommand_args=self.standard_args)

        info = re.findall(re.compile(r"^\w+\s+INFO.*", re.MULTILINE),
                          r.proc.stdout)
        self.assertGreater(len(info), 0)

        debug = re.findall(re.compile(r"^\w+\s+DEBUG.*", re.MULTILINE),
                           r.proc.stdout)
        self.assertGreater(len(debug), 0)

        self.basic_checks(r)

    def test_standard_args_verbosity_quiet(self):
        r = run_cli('tests/assets/platform_valid/sot.csv',
                    verbosity_arg='-q', subcommand_args=self.standard_args)

        info = re.findall(re.compile(r"^\w+\s+INFO.*", re.MULTILINE),
                          r.proc.stdout)
        self.assertEqual(0, len(info))

        debug = re.findall(re.compile(r"^\w+\s+DEBUG.*", re.MULTILINE),
                           r.proc.stdout)
        self.assertEqual(0, len(debug))

        self.basic_checks(r)

    def test_standard_args_cluster_selector(self):
        args = [*self.standard_args, "--cluster-name", "US62877CLS01"]
        r = run_cli('tests/assets/platform_valid/sot.csv', subcommand_args=args)

        self.assertIn("1 clusters total, all rendered successfully",
                      r.proc.stdout)
        self.assertEqual("", r.proc.stderr)
        self.assertTrue(
            r.out.joinpath("nonprod-us/US62877CLS01.yaml").is_file())
        self.assertFalse(
            r.out.joinpath("nonprod-us/US87746CLS01.yaml").is_file())
        self.assertFalse(r.out.joinpath("prod-us").is_dir())
        self.assertFalse(r.out.joinpath("prod-us/US75911CLS01.yaml").is_file())
        self.assertFalse(r.out.joinpath("prod-us/US41273CLS01.yaml").is_file())

        r.temp.cleanup()

    def test_standard_args_group_selector(self):
        args = [*self.standard_args, "--cluster-group", "nonprod-us"]
        r = run_cli('tests/assets/platform_valid/sot.csv', subcommand_args=args)

        self.assertIn("2 clusters total, all rendered successfully",
                      r.proc.stdout)
        self.assertEqual("", r.proc.stderr)
        self.assertFalse(r.out.joinpath("prod-us").is_dir())
        self.assertFalse(r.out.joinpath("prod-us/US75911CLS01.yaml").is_file())
        self.assertFalse(r.out.joinpath("prod-us/US41273CLS01.yaml").is_file())
        self.assertTrue(
            r.out.joinpath("nonprod-us/US62877CLS01.yaml").is_file())
        self.assertTrue(
            r.out.joinpath("nonprod-us/US87746CLS01.yaml").is_file())

        r.temp.cleanup()

    def test_standard_args_tag_selector_single(self):
        args = [*self.standard_args, "--cluster-tag", "donotupgrade"]
        r = run_cli('tests/assets/platform_valid/sot.csv', subcommand_args=args)

        self.assertIn("2 clusters total, all rendered successfully",
                      r.proc.stdout)
        self.assertEqual("", r.proc.stderr)
        self.assertTrue(r.out.joinpath("prod-us").is_dir())
        self.assertTrue(r.out.joinpath("prod-us/US75911CLS01.yaml").is_file())
        self.assertFalse(r.out.joinpath("prod-us/US41273CLS01.yaml").is_file())
        self.assertTrue(
            r.out.joinpath("nonprod-us/US62877CLS01.yaml").is_file())
        self.assertFalse(
            r.out.joinpath("nonprod-us/US87746CLS01.yaml").is_file())

        r.temp.cleanup()

    def test_standard_args_tag_selector_multiple(self):
        args = [*self.standard_args,
                "--cluster-tag", "donotupgrade",
                "--cluster-tag", "corp"]
        r = run_cli('tests/assets/platform_valid/sot.csv', subcommand_args=args)

        self.assertIn("3 clusters total, all rendered successfully",
                      r.proc.stdout)
        self.assertEqual("", r.proc.stderr)
        self.assertTrue(r.out.joinpath("prod-us/US75911CLS01.yaml").is_file())
        self.assertTrue(r.out.joinpath("prod-us/US41273CLS01.yaml").is_file())
        self.assertTrue(
            r.out.joinpath("nonprod-us/US62877CLS01.yaml").is_file())
        self.assertFalse(
            r.out.joinpath("nonprod-us/US87746CLS01.yaml").is_file())

        r.temp.cleanup()

    def test_standard_args_output_subdir_group_explicit(self):
        args = [*self.standard_args, "--output-subdir", "group"]
        r = run_cli('tests/assets/platform_valid/sot.csv', subcommand_args=args)
        self.basic_checks(r)

    def test_standard_args_output_subdir_cluster(self):
        args = [*self.standard_args, "--output-subdir", "cluster"]
        r = run_cli('tests/assets/platform_valid/sot.csv', subcommand_args=args)

        self.assertEqual(0, r.proc.returncode)
        self.assertIn("4 clusters total, all rendered successfully",
                      r.proc.stdout)
        self.assertEqual("", r.proc.stderr)
        with open('tests/assets/platform_valid/sot.csv') as f:
            reader = csv.reader(f)
            next(reader)  # discard header
            for name, group, _, _ in reader:
                output_file = r.out.joinpath(f'{name}/{name}.yaml')
                self.assertTrue(output_file.exists()),
                self.assertTrue(output_file.is_file())

        r.temp.cleanup()

    def test_standard_args_output_subdir_none(self):
        args = [*self.standard_args, "--output-subdir", "none"]
        r = run_cli('tests/assets/platform_valid/sot.csv', subcommand_args=args)

        self.assertEqual(0, r.proc.returncode)
        self.assertIn("4 clusters total, all rendered successfully",
                      r.proc.stdout)
        self.assertEqual("", r.proc.stderr)
        with open('tests/assets/platform_valid/sot.csv') as f:
            reader = csv.reader(f)
            next(reader)  # discard header
            for name, group, _, _ in reader:
                output_file = r.out.joinpath(f'{name}.yaml')
                self.assertTrue(output_file.exists()),
                self.assertTrue(output_file.is_file())

        r.temp.cleanup()

    def test_standard_args_split_output(self):
        args = [*self.standard_args, "--split-output"]
        r = run_cli('tests/assets/platform_valid/sot.csv', subcommand_args=args)

        self.assertEqual(0, r.proc.returncode)
        self.assertIn("4 clusters total, all rendered successfully",
                      r.proc.stdout)
        self.assertFalse(r.proc.stderr)
        with open('tests/assets/platform_valid/sot.csv') as f:
            reader = csv.reader(f)
            next(reader)  # discard header
            for name, group, _, _ in reader:
                output_file = r.out.joinpath(f'{name}/{name}.yaml')
                self.assertFalse(output_file.exists())

                # limiting validation to checking for presence of a few dirs and files
                output_dirs = [
                    r.out.joinpath(f'{group}/{name}/clusterdns'),
                    r.out.joinpath(f'{group}/{name}/rbac'),
                    r.out.joinpath(f'{group}/{name}/robin'),
                    r.out.joinpath(f'{group}/{name}/vmruntime'),
                ]

                # US62877CLS01 configured in SoT to use base_library/experimental/vmruntime
                if name == 'US62877CLS01':
                    del output_dirs[-1]
                    output_dirs.append(r.out.joinpath(f'{group}/{name}/experimental/vmruntime'))

                for d in output_dirs:
                    self.assertTrue(d.exists())
                    self.assertTrue(d.is_dir())

                output_files = [
                    r.out.joinpath(f'{group}/{name}/clusterdns/clusterdns.yaml'),
                    r.out.joinpath(f'{group}/{name}/rbac/gateway-connect.yaml'),
                    r.out.joinpath(f'{group}/{name}/robin/robin-cli-pod.yaml'),
                    r.out.joinpath(f'{group}/{name}/vmruntime/enable-vmruntime.yaml'),
                ]
                if name == 'US62877CLS01':
                    del output_files[-1]
                    filepath = r.out.joinpath(
                        f'{group}/{name}/experimental/vmruntime/enable-vmruntime.yaml')
                    output_files.append(filepath)
                    with open(filepath) as f:
                        doc = yaml.safe_load(f)
                    self.assertEqual(doc['spec']['vmImageFormat'], "raw")

                for f in output_files:
                    self.assertTrue(f.exists())
                    self.assertTrue(f.is_file())

        r.temp.cleanup()


class TestClusterHydrationPlatformErrorCases(unittest.TestCase):
    standard_args = args = [
        '-b', 'tests/assets/platform_invalid/base_library',
        '-o', 'tests/assets/platform_invalid/overlays',
    ]

    def test_bad_csv_no_name(self):
        r = run_cli(
            'tests/assets/platform_invalid/sot_invalid_missing_name.csv',
            subcommand_args=self.standard_args)

        self.assertNotEqual(0, r.proc.returncode,
                            msg="exit code should not be zero")
        match = re.search(re.compile(r"ERROR.*cluster_name",
                                     flags=re.MULTILINE | re.IGNORECASE),
                          r.proc.stderr)
        self.assertTrue(match, msg="no error output or not matching")

        r.temp.cleanup()

    def test_bad_csv_no_group(self):
        r = run_cli(
            'tests/assets/platform_invalid/sot_invalid_missing_group.csv',
            subcommand_args=self.standard_args)

        self.assertNotEqual(0, r.proc.returncode,
                            msg="exit code should not be zero")
        match = re.search(
            re.compile(r"\s*\w+\s+ERROR\s+Could not find column 'cluster_group' in source",
                       flags=re.MULTILINE | re.IGNORECASE),
            r.proc.stderr)
        self.assertTrue(match, msg="no error output or not matching")

        r.temp.cleanup()

    def test_bad_csv_no_tags(self):
        r = run_cli(
            'tests/assets/platform_invalid/sot_invalid_missing_tags.csv',
            subcommand_args=self.standard_args)

        self.assertNotEqual(0, r.proc.returncode,
                            msg='exit code should not be zero')
        match = re.search(re.compile(r"ERROR.*cluster_tags",
                                     flags=re.MULTILINE | re.IGNORECASE),
                          r.proc.stderr)
        self.assertTrue(match, msg='no error output or not matching')
        r.temp.cleanup()

    def test_bad_csv_empty(self):
        r = run_cli('tests/assets/platform_invalid/sot_invalid_empty.csv',
                    subcommand_args=self.standard_args)
        self.assertEqual(0, r.proc.returncode,
                         msg='exit code should be zero')
        self.assertIn('0 clusters total, all rendered successfully',
                      r.proc.stdout)
        r.temp.cleanup()

    # TODO: fix so this test case passes
    # does not catch and exit non-zero for the unicode decode error
    # noncritical as csv validation catches this
    @unittest.skip
    def test_bad_csv_binary(self):
        r = run_cli(
            'tests/assets/platform_invalid/sot_invalid_random_bytes.csv',
            subcommand_args=self.standard_args)
        self.assertNotEqual(0, r.proc.returncode,
                            msg="exit code should not be zero")
        match = re.search(
            re.compile(r"ERROR.*CSV", flags=re.MULTILINE | re.IGNORECASE),
            r.proc.stderr)
        self.assertTrue(match, msg="no error output or not matching")
        r.temp.cleanup()

    def test_bad_csv_header(self):
        r = run_cli(
            'tests/assets/platform_invalid/sot_invalid_header.csv',
            subcommand_args=self.standard_args)
        self.assertNotEqual(0, r.proc.returncode,
                            msg="exit code should not be zero")
        match = re.search(
            re.compile(r"\s*\w+\s+ERROR\s+Source of truth file missing cluster_name column",
                       flags=re.MULTILINE | re.IGNORECASE),
            r.proc.stderr)
        self.assertTrue(match, msg="no error output or not matching")
        r.temp.cleanup()

    def test_bad_jinja(self):
        args = [*self.standard_args, '--cluster-group', "badjinja"]
        r = run_cli('tests/assets/platform_invalid/sot_valid.csv',
                    subcommand_args=args)
        self.assertNotEqual(0, r.proc.returncode,
                            msg="exit code should not be zero")
        match = re.search(
            re.compile(r"ERROR.*template", flags=re.MULTILINE | re.IGNORECASE),
            r.proc.stderr)
        self.assertTrue(match, msg='no error output or not matching')
        self.assertIn('Cluster US87747CLS01 failed: jinja', r.proc.stderr)
        r.temp.cleanup()

    def test_bad_yaml_traditional_output(self):
        # the bad YAML is going to pass through kustomize, resulting in an accumulation failure.
        # in traditional output there is no YAML parsing done in hydrator, so we expect to
        # encounter the YAML parse problem when kustomize finds it
        args = [*self.standard_args, '--cluster-group', 'badyaml']
        r = run_cli('tests/assets/platform_invalid/sot_valid.csv', subcommand_args=args)
        self.assertNotEqual(0, r.proc.returncode, msg='exit code should not be zero')
        match = re.search(
            re.compile(r"kustomize.*WARNING", flags=re.MULTILINE | re.IGNORECASE),
            r.proc.stderr)
        self.assertTrue(match, msg='no error output or not matching')
        self.assertIn("Cluster US87749CLS01 failed: kustomize", r.proc.stderr)
        r.temp.cleanup()

    def test_bad_yaml_split_output(self):
        # when using split output, we expect to find the bad YAML when YAML-parsing the many
        # documents, which we load before running kustomize.  when this happens, we dump the
        # appropriate error to stderr and check for it
        args = [*self.standard_args, '--split-output', '--cluster-group', 'badyaml']
        r = run_cli('tests/assets/platform_invalid/sot_valid.csv', subcommand_args=args)
        self.assertNotEqual(0, r.proc.returncode, msg='exit code should not be zero')
        match = re.search(
            re.compile(
                r"hydrator\s+ERROR\s+\w+: Not proceeding due to errors.*"
                r"while parsing a block mapping", flags=re.MULTILINE | re.IGNORECASE),
            r.proc.stderr)
        self.assertTrue(match, msg='no error output or not matching')
        r.temp.cleanup()

    def test_bad_kustomize(self):
        args = [*self.standard_args, '--cluster-group', 'badkustomize']
        r = run_cli('tests/assets/platform_invalid/sot_valid.csv',
                    subcommand_args=args)
        self.assertNotEqual(0, r.proc.returncode,
                            msg='exit code should not be zero')
        match = re.search(
            re.compile(r'WARNING.*kustomize', flags=re.MULTILINE | re.IGNORECASE),
            r.proc.stderr)
        self.assertTrue(match, msg='no error output or not matching')
        self.assertIn('US87748CLS01 failed: kustomize', r.proc.stderr)
        r.temp.cleanup()

    def test_bad_kustomize_uncommon_template_ancestry(self):
        new_base = tempfile.TemporaryDirectory()
        new_overlay = tempfile.TemporaryDirectory()

        cp = shutil.which('cp')
        subprocess.run([cp, '-a', 'tests/assets/platform_invalid/base_library',
                        new_base.name])
        subprocess.run([cp, '-a', 'tests/assets/platform_invalid/overlays',
                        new_overlay.name])

        args = ['-b', new_base.name + '/base_library',
                '-o', new_overlay.name + '/overlays',
                '--cluster-group', 'badkustomize']
        r = run_cli('tests/assets/platform_invalid/sot_valid.csv',
                    subcommand_args=args)
        self.assertNotEqual(0, r.proc.returncode,
                            msg="exit code should not be zero")
        match = re.search(
            re.compile(r'WARNING.*kustomize', flags=re.MULTILINE | re.IGNORECASE),
            r.proc.stderr)
        self.assertTrue(match, msg='no error output or not matching')
        self.assertIn('Cluster US87748CLS01 failed: kustomize', r.proc.stderr)

        new_base.cleanup()
        new_overlay.cleanup()
        r.temp.cleanup()


if __name__ == '__main__':
    unittest.main()
