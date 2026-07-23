# chimera-lna

Chimera plugin for the LNA observatory (OPD, Brazópolis, Brazil) dome and weather station.

This is a plugin for the [Chimera observatory control system](https://github.com/astroufsc/chimera).

It provides:

- `DomeLNA`: driver for the COTE/LNA custom dome (serial "MEADE" protocol), including the
  dome flat-field lamp.
- `OpdWeather`: weather station instrument backed by the LNA weather HTTP API.
- End-to-end hardware simulators (`chimera_lna.simulators`): a TCP server that speaks the
  dome controller protocol and an HTTP server that mimics the LNA weather API, so the real
  drivers can be exercised without the real hardware.

## Installation

```bash
pip install -U chimera_lna
```

Or install from source:

```bash
pip install -U git+https://github.com/astroufsc/chimera-lna.git
```

## Configuration Example

Add the following to your `chimera.config` file:

```yaml
dome:
  - name: dome
    type: DomeLNA
    device: /dev/ttyS0
    telescope: /Telescope/0

weatherstations:
  - name: opd_weather
    type: OpdWeather
    api_url: https://200.131.64.237:8088/api/weather-now/
```

## Hardware Simulators

The plugin ships standalone simulators of the LNA hardware. They are real
servers - not fake chimera objects - so the actual `DomeLNA` and `OpdWeather`
drivers talk to them exactly as they would talk to the hardware:

```bash
# dome controller simulator (MEADE serial protocol over TCP)
python -m chimera_lna.simulators.dome --port 5001

# weather API simulator (HTTP, same JSON schema as the LNA API)
python -m chimera_lna.simulators.weather --port 8088
```

Then point the instruments at them in `chimera.config`:

```yaml
dome:
  - name: dome
    type: DomeLNA
    device: socket://127.0.0.1:5001

weatherstations:
  - name: opd_weather
    type: OpdWeather
    api_url: http://127.0.0.1:8088/api/weather-now/
```

`DomeLNA` opens its device with pyserial's `serial_for_url`, so `device`
accepts both real serial ports (`/dev/ttyS0`) and `socket://host:port` URLs.
The test suite uses these simulators to run the drivers through the full
chimera Manager lifecycle.

## Development

### Setup Development Environment

```bash
# Clone the repository
git clone https://github.com/astroufsc/chimera-lna.git
cd chimera-lna

# Install dependencies
uv sync

# Install pre-commit hooks
uv run pre-commit install --install-hooks
```

### Running Tests

```bash
uv run pytest
```

### Code Quality

This project uses:
- [Ruff](https://docs.astral.sh/ruff/) for linting and formatting
- [pre-commit](https://pre-commit.com/) for automated checks

```bash
# Run linter
uv run ruff check

# Run formatter
uv run ruff format

# Run all pre-commit hooks
uv run pre-commit run --all-files
```

## License

GPL-2.0-or-later

## Contact

For more information, contact us on chimera's discussion list:
https://groups.google.com/forum/#!forum/chimera-discuss

Bug reports and patches are welcome and can be sent over our GitHub page:
https://github.com/astroufsc/chimera-lna
