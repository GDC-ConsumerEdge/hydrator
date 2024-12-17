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
import csv
import re
import sys
import time
import unittest

import yaml

from tests.test_cluster_cli import run_cli


class TestClusterHydrationPlatformValidCasesAsync(unittest.TestCase):
    standard_args = [
        '-b', 'tests/assets/platform_valid_async/base_library',
        '-o', 'tests/assets/platform_valid_async/overlays',
    ]

    def run_cli_async(self, **kwargs):
        default_kwargs = {
            'main_args': ['--workers', '25'],
            'subcommand_args': self.standard_args,
        }
        default_kwargs.update(kwargs)
        self.r = run_cli('tests/assets/platform_valid_async/sot.csv', **default_kwargs)

    def setUp(self):
        # if '-v' in sys.argv:
        #     print('-v in sys.argv')
        self.start_time = time.time()

    def tearDown(self):
        t = time.time() - self.start_time
        if '-v' in sys.argv:
            print(f' runtime {t:0.3f}s ', end='', flush=True)
        elif '-q' not in sys.argv:
            print(f'{self.id()} runtime {t:0.3f}s ')

        self.r.temp.cleanup()

    def basic_checks(self):
        self.assertEqual(0, self.r.proc.returncode)
        self.assertIn("50 clusters total, all rendered successfully",
                      self.r.proc.stdout)
        self.assertEqual("", self.r.proc.stderr)
        with open('tests/assets/platform_valid_async/sot.csv') as f:
            reader = csv.reader(f)
            next(reader)  # discard header
            for name, group, _, _ in reader:
                output_file = self.r.out.joinpath(f'{group}/{name}.yaml')
                self.assertTrue(output_file.exists()),
                self.assertTrue(output_file.is_file())

    def test_standard_args(self):
        self.run_cli_async()
        self.basic_checks()

    def test_standard_args_sync_baseline(self):
        self.r = run_cli('tests/assets/platform_valid_async/sot.csv',
                         subcommand_args=self.standard_args)
        self.basic_checks()

    def test_standard_args_verbosity_one(self):
        self.run_cli_async()

        info = re.findall(re.compile(r"^\w+\s+INFO.*", re.MULTILINE), self.r.proc.stdout)
        self.assertGreater(len(info), 0)

        debug = re.findall(re.compile(r"^\w+\s+DEBUG.*", re.MULTILINE), self.r.proc.stdout)
        self.assertEqual(0, len(debug))

        self.basic_checks()

    def test_standard_args_verbosity_two(self):
        self.run_cli_async(verbosity_arg='-vv')

        info = re.findall(re.compile(r"^\w+\s+INFO.*", re.MULTILINE), self.r.proc.stdout)
        self.assertGreater(len(info), 0)

        debug = re.findall(re.compile(r"^\w+\s+DEBUG.*", re.MULTILINE), self.r.proc.stdout)
        self.assertGreater(len(debug), 0)

        self.basic_checks()

    def test_standard_args_verbosity_quiet(self):
        self.run_cli_async(verbosity_arg='-q')

        info = re.findall(re.compile(r"^\w+\s+INFO.*", re.MULTILINE), self.r.proc.stdout)
        self.assertEqual(0, len(info))

        debug = re.findall(re.compile(r"^\w+\s+DEBUG.*", re.MULTILINE), self.r.proc.stdout)
        self.assertEqual(0, len(debug))

        self.basic_checks()

    def test_standard_args_cluster_selector(self):
        args = [*self.standard_args, "--cluster-name", "US12350CLS01"]
        self.run_cli_async(subcommand_args=args)

        self.assertIn("1 clusters total, all rendered successfully", self.r.proc.stdout)
        self.assertEqual("", self.r.proc.stderr)
        self.assertTrue(self.r.out.joinpath("nonprod-us/US12350CLS01.yaml").is_file())
        self.assertFalse(self.r.out.joinpath("nonprod-us/US87746CLS01.yaml").is_file())
        self.assertFalse(self.r.out.joinpath("prod-us").is_dir())
        self.assertFalse(self.r.out.joinpath("prod-us/US75911CLS01.yaml").is_file())
        self.assertFalse(self.r.out.joinpath("prod-us/US41273CLS01.yaml").is_file())

    def test_standard_args_group_selector(self):
        args = [*self.standard_args, "--cluster-group", "nonprod-us"]
        self.run_cli_async(subcommand_args=args)

        self.assertIn("25 clusters total, all rendered successfully", self.r.proc.stdout)
        self.assertEqual("", self.r.proc.stderr)
        self.assertFalse(self.r.out.joinpath("prod-us").is_dir())

        files = {p.stem for p in self.r.out.joinpath("nonprod-us").iterdir() if p.is_file()}

        with open('tests/assets/platform_valid_async/sot.csv') as f:
            reader = csv.reader(f)
            next(reader)
            nonprod = {i[0] for i in reader if i[1] == 'nonprod-us'}

        # should have as many output files as there are rows in nonprod-us
        self.assertEqual(files, nonprod)

    def test_standard_args_tag_selector_single(self):
        args = [*self.standard_args, "--cluster-tag", "single-selector-test"]
        self.run_cli_async(subcommand_args=args)

        self.assertIn("2 clusters total, all rendered successfully", self.r.proc.stdout)
        self.assertEqual("", self.r.proc.stderr)

        self.assertTrue(self.r.out.joinpath("prod-us/US10981CLS01.yaml").is_file())
        self.assertEqual(
            1, len([p for p in self.r.out.joinpath("prod-us").iterdir() if p.is_file()]))

        self.assertTrue(self.r.out.joinpath("nonprod-us/US11312CLS01.yaml").is_file())
        self.assertEqual(
            1, len([p for p in self.r.out.joinpath("nonprod-us").iterdir() if p.is_file()]))

    def test_standard_args_tag_selector_multiple(self):
        args = [*self.standard_args, "--cluster-tag", "foo", "--cluster-tag", "bar"]
        self.run_cli_async(subcommand_args=args)

        self.assertIn("2 clusters total, all rendered successfully", self.r.proc.stdout)
        self.assertEqual("", self.r.proc.stderr)

        self.assertTrue(self.r.out.joinpath("prod-us/US10644CLS01.yaml").is_file())
        self.assertEqual(
            1, len([p for p in self.r.out.joinpath("prod-us").iterdir() if p.is_file()]))

        self.assertTrue(self.r.out.joinpath("nonprod-us/US11137CLS01.yaml").is_file())
        self.assertEqual(
            1, len([p for p in self.r.out.joinpath("nonprod-us").iterdir() if p.is_file()]))

    def test_standard_args_output_subdir_group_explicit(self):
        args = [*self.standard_args, "--output-subdir", "group"]
        self.run_cli_async(subcommand_args=args)
        self.basic_checks()

    def test_standard_args_output_subdir_cluster(self):
        args = [*self.standard_args, "--output-subdir", "cluster"]
        self.run_cli_async(subcommand_args=args)

        self.assertEqual(0, self.r.proc.returncode)
        self.assertIn("50 clusters total, all rendered successfully", self.r.proc.stdout)
        self.assertEqual("", self.r.proc.stderr)
        with open('tests/assets/platform_valid_async/sot.csv') as f:
            reader = csv.reader(f)
            next(reader)  # discard header
            for name, group, _, _ in reader:
                output_file = self.r.out.joinpath(f'{name}/{name}.yaml')
                self.assertTrue(output_file.exists()),
                self.assertTrue(output_file.is_file())

    def test_standard_args_output_subdir_none(self):
        args = [*self.standard_args, "--output-subdir", "none"]
        self.run_cli_async(subcommand_args=args)

        self.assertEqual(0, self.r.proc.returncode)
        self.assertIn("50 clusters total, all rendered successfully", self.r.proc.stdout)
        self.assertEqual("", self.r.proc.stderr)
        with open('tests/assets/platform_valid_async/sot.csv') as f:
            reader = csv.reader(f)
            next(reader)  # discard header
            for name, group, _, _ in reader:
                output_file = self.r.out.joinpath(f'{name}.yaml')
                self.assertTrue(output_file.exists()),
                self.assertTrue(output_file.is_file())

    def test_standard_args_split_output(self):
        args = [*self.standard_args, "--split-output"]
        self.run_cli_async(subcommand_args=args)

        self.assertEqual(0, self.r.proc.returncode)
        self.assertIn("50 clusters total, all rendered successfully", self.r.proc.stdout)
        self.assertFalse(self.r.proc.stderr)

        with open('tests/assets/platform_valid_async/sot.csv') as f:
            reader = csv.reader(f)
            next(reader)  # discard header
            for name, group, _, _ in reader:
                output_file = self.r.out.joinpath(f'{name}/{name}.yaml')
                self.assertFalse(output_file.exists())

                # limiting validation to checking for presence of a few dirs and files
                output_dirs = [
                    self.r.out.joinpath(f'{group}/{name}/clusterdns'),
                    self.r.out.joinpath(f'{group}/{name}/rbac'),
                    self.r.out.joinpath(f'{group}/{name}/robin'),
                    self.r.out.joinpath(f'{group}/{name}/vmruntime'),
                ]

                # US12761CLS01 configured in SoT to use base_library/experimental/vmruntime
                if name == 'US12761CLS01':
                    del output_dirs[-1]
                    output_dirs.append(
                        self.r.out.joinpath(f'{group}/{name}/experimental/vmruntime'))

                for d in output_dirs:
                    self.assertTrue(d.exists())
                    self.assertTrue(d.is_dir())

                output_files = [
                    self.r.out.joinpath(f'{group}/{name}/clusterdns/clusterdns.yaml'),
                    self.r.out.joinpath(f'{group}/{name}/rbac/gateway-connect.yaml'),
                    self.r.out.joinpath(f'{group}/{name}/robin/robin-cli-pod.yaml'),
                    self.r.out.joinpath(f'{group}/{name}/vmruntime/enable-vmruntime.yaml'),
                ]
                if name == 'US12761CLS01':
                    del output_files[-1]
                    filepath = self.r.out.joinpath(
                        f'{group}/{name}/experimental/vmruntime/enable-vmruntime.yaml')
                    output_files.append(filepath)
                    with open(filepath) as f:
                        doc = yaml.safe_load(f)
                    self.assertEqual(doc['spec']['vmImageFormat'], "raw")

                for f in output_files:
                    self.assertTrue(f.exists())
                    self.assertTrue(f.is_file())
