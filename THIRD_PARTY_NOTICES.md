# Third-party notices

Pinned dependencies retain their own licenses, reproduced in `LICENSES/`. The project’s MIT license does not replace them.

| Dependency | Version | License | Source |
| --- | --- | --- | --- |
| Telethon | 1.45.0 | MIT | [Source](https://codeberg.org/Lonami/Telethon) |
| python-socks | 3.1.1 | Apache-2.0 | [Source](https://github.com/romis2012/python-socks) |
| async-timeout | 5.0.1 | Apache-2.0 | [Source](https://github.com/aio-libs/async-timeout) |
| PySocks | 1.7.1 | BSD-3-Clause | [Source](https://github.com/Anorov/PySocks) |
| pyaes | 1.6.1 | MIT | [Source](https://github.com/ricmoo/pyaes) |
| rsa | 4.9.1 | Apache-2.0 | [Source](https://github.com/sybrenstuvel/python-rsa) |
| pyasn1 | 0.6.4 | BSD-2-Clause | [Source](https://github.com/pyasn1/pyasn1) |

Windows packages include official CPython 3.13.16 and its runtime/LICENSE.txt and third-party notices. The source repository does not include that runtime. Builders copy dependency licenses from dist-info; preserve upstream Apache NOTICE files where present.

See requirements.txt, package BUILD-INFO.json and [BUILD.md](docs/BUILD.md) for hashes, provenance and packaging.
