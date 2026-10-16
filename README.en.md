# QingYun Tea Control / 青韵智控

*Multimodal Sensing and Adaptive Control Research Platform for Phoenix Dancong Tea Processing*

[中文说明](README.md) · [Getting Started](docs/GETTING_STARTED.md) · [Architecture](docs/ARCHITECTURE.md) · [Project Status](docs/PROJECT_STATUS.md) · [Contributing](CONTRIBUTING.md)

QingYun Tea Control is an open-source **research prototype** for the *zuoqing* stage of Phoenix Dancong oolong tea production, focused on leaf shaking and resting. It explores environmental, gas, temperature and mass sensing, traceable batch records, operator annotations, replay and eventually state-informed process control. It **does not** implement kill-green, rolling or drying.

**Important maturity note:** The complete state-estimation-to-actuation feedback loop has **not** been validated with real tea batches, and there is no established improvement in finished-tea quality. A successful build, simulated input and summary workbook must not be interpreted as certification of a physical machine.

## Components

| Component | Path |
| --- | --- |
| ESP32-S3 firmware, GPIO and actuator prototypes | [hardware/](hardware) |
| Python/PySide6 acquisition, operator events, replay and analysis | [software/host_mvp/](software/host_mvp) |
| Firmware and desktop regression tests | [hardware/esp32_s3_mvp/test/](hardware/esp32_s3_mvp/test) and [software/host_mvp/](software/host_mvp) |
| Curated hardware result workbook and limitations | [data/hardware_tests/](data/hardware_tests) |
| Architecture, experimental procedures and development records | [docs/](docs) |
| Historical student competition submission materials | [submission/](submission) |

## Run the desktop demo

With Python 3.11 or 3.12 on Windows, from the repository root:

~~~powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r software/host_mvp/requirements.txt
cd software/host_mvp
..\..\.venv\Scripts\python.exe app.py --simulate
~~~

On Linux/macOS, use `./.venv/bin/python` instead of the Windows virtual-environment executable. For hardware bring-up, build targets, tests and data handling, use [Getting Started](docs/GETTING_STARTED.md).

## What is and is not validated

The default branch contains acquisition and host application code plus regression tests. Firmware construction and simulated/system-level software tests are documented, but the current firmware/hardware combination requires independent board-level acceptance, sensor calibration, long-duration and electrical safety verification. The curated Excel file is **not** the full raw sensor log. The offline modeling and shadow-suggestion work is in [PR #9](https://github.com/crazyname/tiaozhan/pull/9), **not** part of the default branch.

Read the [status matrix](docs/PROJECT_STATUS.md) before citing results or applying the software to physical equipment.

## License and safety

Project-authored source code (firmware, desktop software and scripts) is under the [MIT License](LICENSE). The rights to competition materials, imagery, datasets and third-party components are not granted merely by being hosted here; see [NOTICE](NOTICE.md).

The control code is experimental and not a safety-certified controller. Physical emergency stop and safe power isolation must be engineered independently. Report suspected security or privacy issues via the process in [SECURITY.md](SECURITY.md).
