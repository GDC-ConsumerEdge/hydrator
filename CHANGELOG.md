# Changelog

## [2.2.1](https://github.com/GDC-ConsumerEdge/hydrator/compare/v2.2.0...v2.2.1) (2026-05-08)


### Bug Fixes

* fix YAML dumping for strings with leading zeros to be preserved as strings (e.g. octets) ([#38](https://github.com/GDC-ConsumerEdge/hydrator/issues/38)) ([a03ffdc](https://github.com/GDC-ConsumerEdge/hydrator/commit/a03ffdca52002f6e20ca65c5eef6f751c14039a7))

## [2.2.0](https://github.com/GDC-ConsumerEdge/hydrator/compare/v2.1.0...v2.2.0) (2025-10-08)


### Features

* **ci:** add ghcr publish workflow and optimize triggers ([94c5e1d](https://github.com/GDC-ConsumerEdge/hydrator/commit/94c5e1da9ca90aa3b6c430adc158d359968d576c))
* use multiprocessing workers for enhanced performance ([94c5e1d](https://github.com/GDC-ConsumerEdge/hydrator/commit/94c5e1da9ca90aa3b6c430adc158d359968d576c))
* use multiprocessing workers for enhanced performance ([#36](https://github.com/GDC-ConsumerEdge/hydrator/issues/36)) ([94c5e1d](https://github.com/GDC-ConsumerEdge/hydrator/commit/94c5e1da9ca90aa3b6c430adc158d359968d576c))


### Bug Fixes

* bug in KRM parser ([94c5e1d](https://github.com/GDC-ConsumerEdge/hydrator/commit/94c5e1da9ca90aa3b6c430adc158d359968d576c))


### Documentation

* update README.md ([1eacd00](https://github.com/GDC-ConsumerEdge/hydrator/commit/1eacd00fc5df930f024f1223c37c7b67ef5abb5f))
* Update README.md ([#31](https://github.com/GDC-ConsumerEdge/hydrator/issues/31)) ([c56dcca](https://github.com/GDC-ConsumerEdge/hydrator/commit/c56dcca9fbadf6f6675d4d9d0605e4492015b282))

## [2.1.0](https://github.com/GDC-ConsumerEdge/hydrator/compare/v2.0.0...v2.1.0) (2025-04-29)


### Features

* significantly improve YAML peformance with workers ([#20](https://github.com/GDC-ConsumerEdge/hydrator/issues/20)) ([033e6fd](https://github.com/GDC-ConsumerEdge/hydrator/commit/033e6fd462d993dd105cc38e9de5da693cc5aa45))

## [2.0.0](https://github.com/GDC-ConsumerEdge/hydrator/compare/v1.0.0...v2.0.0) (2025-03-31)


### ⚠ BREAKING CHANGES

* rename "package" subcommand to "group" ([#7](https://github.com/GDC-ConsumerEdge/hydrator/issues/7))

### Features

* adds support for default overlays ([b457035](https://github.com/GDC-ConsumerEdge/hydrator/commit/b457035b3335bb649a43448d51cc78ab22cf14a6))
* expand cluster and group name selectors to be provided more than once ([c067517](https://github.com/GDC-ConsumerEdge/hydrator/commit/c0675172218ba553c2d1730fb1899ae75dc62ab8))
* rename "package" subcommand to "group" ([#7](https://github.com/GDC-ConsumerEdge/hydrator/issues/7)) ([0e83765](https://github.com/GDC-ConsumerEdge/hydrator/commit/0e83765429e6b24afaf3d3c6407cb3c09024fc85))


### Bug Fixes

* better CSV/SoT parsing ([c067517](https://github.com/GDC-ConsumerEdge/hydrator/commit/c0675172218ba553c2d1730fb1899ae75dc62ab8))
* **hydration:** creates a temporary directory per async worker where before ([b457035](https://github.com/GDC-ConsumerEdge/hydrator/commit/b457035b3335bb649a43448d51cc78ab22cf14a6))

## 1.0.0 (2025-03-24)


### Miscellaneous Chores

* release 1.0.0 ([0a587c2](https://github.com/GDC-ConsumerEdge/hydrator/commit/0a587c218fb26a4c053e6b869098ef812fa33c77))
