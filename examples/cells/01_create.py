"""
Example: Creating cell specifications, instances, and measurements.

This example demonstrates the new nested cell specification API with:
- Structured ratings (capacity, voltage limits with units)
- Nested component/material definitions
- Quantity format for numeric properties (value + unit)
- Measured properties on cell instances
"""

from ionworks import Ionworks
import pandas as pd

client = Ionworks()

# Step 1: Create a new cell spec with nested component/material data
# The new API uses:
# - `ratings` object with Quantity format for electrical specs
# - Nested `anode`, `cathode`, etc. with `properties` and `material` objects
# - `Quantity` format: {"value": <number>, "unit": "<unit>"} for numeric properties

cell_spec = client.cell_spec.create_or_get(
    {
        "name": "NCM622/Graphite Coin Cell",
        "form_factor": "R2032",
        "manufacturer": "Custom Cells",
        # Electrical ratings with units
        "ratings": {
            "capacity": {"value": 0.002, "unit": "A.h"},
            "voltage_min": {"value": 2.5, "unit": "V"},
            "voltage_max": {"value": 4.2, "unit": "V"},
            "nominal_voltage": {"value": 3.6, "unit": "V"},
            "energy": {"value": 7.2, "unit": "mW.h"},
            "max_discharge_rate": {"value": 2, "unit": "C"},
            "max_charge_rate": {"value": 1, "unit": "C"},
        },
        # Nested anode component with material
        "anode": {
            "properties": {
                "diameter": {"value": 15, "unit": "mm"},
                "thickness": {"value": 45, "unit": "um"},
                "loading": {"value": 6.5, "unit": "mg.cm-2"},
                "porosity": {"value": 35, "unit": "percent"},
                "binder": "PVDF",
                "conductive_additive": "Carbon Black",
            },
            "material": {
                "name": "Graphite",
                "manufacturer": "Customcells",
                "definition": {
                    "type": "graphite",
                    "particle_size_d50": {"value": 15, "unit": "um"},
                },
            },
        },
        # Nested cathode component with material
        "cathode": {
            "properties": {
                "diameter": {"value": 14, "unit": "mm"},
                "thickness": {"value": 52, "unit": "um"},
                "loading": {"value": 12.3, "unit": "mg.cm-2"},
                "porosity": {"value": 30, "unit": "percent"},
                "binder": "PVDF",
                "conductive_additive": "Carbon Black",
            },
            "material": {
                "name": "NCM622",
                "manufacturer": "BASF",
                "definition": {
                    "formula": "LiNi0.6Co0.2Mn0.2O2",
                    "type": "layered_oxide",
                    "specific_capacity": {"value": 180, "unit": "mA.h.g-1"},
                },
            },
        },
        # Nested electrolyte component with material
        "electrolyte": {
            "properties": {
                "volume": {"value": 40, "unit": "uL"},
            },
            "material": {
                "name": "LP57",
                "manufacturer": "Gotion",
                "definition": {
                    "type": "liquid",
                    "salt": "LiPF6",
                    "salt_concentration": {"value": 1.0, "unit": "mol.L-1"},
                    "solvents": ["EC", "EMC"],
                    "solvent_ratio": "3:7 w/w",
                },
            },
        },
        # Nested separator component with material
        "separator": {
            "properties": {
                "diameter": {"value": 16, "unit": "mm"},
                "thickness": {"value": 25, "unit": "um"},
                "porosity": {"value": 40, "unit": "percent"},
            },
            "material": {
                "name": "Celgard 2325",
                "manufacturer": "Celgard",
                "definition": {
                    "type": "trilayer",
                    "composition": "PP/PE/PP",
                },
            },
        },
        # Source/provenance information
        "source": {
            "creator_name": "Battery Lab",
            "creator_orcid": "0000-0001-2345-6789",
            "publication_date": "2024-01-15",
            "license": "CC-BY-4.0",
        },
        # Other design properties
        "properties": {
            "assembly_method": "dry room",
            "formation_cycles": {"value": 3, "unit": "dimensionless"},
        },
        "notes": "High-performance coin cell for rate capability testing",
    }
)

print(f"Created cell spec: {cell_spec.name} (id: {cell_spec.id})")
print(f"  Form factor: {cell_spec.form_factor}")
cap = cell_spec.ratings["capacity"]
print(f"  Capacity: {cap['value']} {cap['unit']}")
v_min = cell_spec.ratings["voltage_min"]
v_max = cell_spec.ratings["voltage_max"]
print(f"  Voltage range: {v_min['value']}-{v_max['value']} V")

# Step 2: Create a cell instance with measured properties
# measured_properties uses the same Quantity format, organized by component
cell_instance = client.cell_instance.create_or_get(
    cell_spec.id,
    {
        "name": "NCM622-GR-001",
        "batch": "BATCH-2024-001",
        "date_manufactured": "2024-01-20",
        # Measured properties per component (as-built values)
        "measured_properties": {
            "cathode": {
                "loading": {"value": 12.1, "unit": "mg.cm-2"},
                "thickness": {"value": 51, "unit": "um"},
            },
            "anode": {
                "loading": {"value": 6.4, "unit": "mg.cm-2"},
                "thickness": {"value": 44, "unit": "um"},
            },
            "cell": {
                "initial_ocv": {"value": 3.02, "unit": "V"},
                "weight": {"value": 3.2, "unit": "g"},
            },
        },
        "notes": "First cell from batch, passed QC inspection",
    },
)

print(f"\nCreated cell instance: {cell_instance.name} (id: {cell_instance.id})")
print(f"  Batch: {cell_instance.batch}")
print(f"  Date manufactured: {cell_instance.date_manufactured}")

# Step 3: Upload measurement with time series data
# Note: positive current = discharge (voltage decreases), negative current = charge
time_series = pd.DataFrame(
    {
        "Time [s]": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
        "Voltage [V]": [3.0, 3.2, 3.5, 3.8, 4.0, 4.1, 4.15, 4.18, 4.19, 4.2],
        "Current [A]": [-0.002] * 10,  # 1C charge (negative = charge)
        "Step count": [0] * 5 + [1] * 5,
        "Cycle count": [0] * 10,
        "Step from cycler": [1] * 5 + [2] * 5,
        "Cycle from cycler": [0] * 10,
        # Cumulative values reset at each step and only increase within step
        "Discharge capacity [A.h]": [0.0] * 10,  # No discharge during charge
        "Charge capacity [A.h]": [
            *[0.0, 0.001, 0.002, 0.003, 0.004],
            *[0.0, 0.001, 0.002, 0.003, 0.004],
        ],
    }
)

measurement_data = {
    "measurement": {
        "name": "Formation Cycle 1",
        "protocol": {
            "name": "CC-CV charge at C/10 to 4.2V, CC discharge at C/10 to 2.5V",
            "ambient_temperature_degc": 25,
        },
        "test_setup": {
            "cycler": "Biologic VMP3",
            "operator": "Jane Smith",
            "lab": "Battery Research Lab",
        },
        "notes": "Formation cycle - first charge",
    },
    "time_series": time_series,
}

try:
    measurement = client.cell_measurement.create(cell_instance.id, measurement_data)
except Exception as e:
    if "duplicate" in str(e).lower() or "409" in str(e):
        # Find and delete existing measurement, then retry
        measurements = client.cell_measurement.list(cell_instance.id)
        for m in measurements:
            if m.name == measurement_data["measurement"]["name"]:
                client.cell_measurement.delete(m.id)
                print(f"Deleted existing measurement: {m.name}")
                break
        measurement = client.cell_measurement.create(cell_instance.id, measurement_data)
    else:
        raise

print(f"\nCreated measurement: {measurement.name}")
if measurement.protocol:
    print(f"  Protocol: {measurement.protocol.get('name')}")
if measurement.test_setup:
    print(f"  Cycler: {measurement.test_setup.get('cycler')}")

# Create another measurement for rate capability testing
# Step 0: discharge (positive current, voltage decreases)
# Step 1: charge (negative current, voltage increases)
rate_test_time_series = pd.DataFrame(
    {
        "Time [s]": list(range(20)),
        "Voltage [V]": (
            [4.2 - 0.06 * i for i in range(10)]  # Discharge: voltage decreases
            + [3.6 + 0.06 * i for i in range(10)]  # Charge: voltage increases
        ),
        "Current [A]": [0.004] * 10 + [-0.004] * 10,  # Discharge then charge
        "Step count": [0] * 10 + [1] * 10,
        "Cycle count": [0] * 20,
        "Step from cycler": [1] * 10 + [2] * 10,
        "Cycle from cycler": [0] * 20,
        # Cumulative values reset at each step
        "Discharge capacity [A.h]": [0.0 + 0.001 * i for i in range(10)]
        + [0.0] * 10,  # Only during discharge
        "Charge capacity [A.h]": [0.0] * 10
        + [0.0 + 0.001 * i for i in range(10)],  # Only during charge
    }
)

rate_test_data = {
    "measurement": {
        "name": "Rate Capability 2C",
        "protocol": {
            "name": "CC charge/discharge at 2C",
            "ambient_temperature_degc": 25,
        },
        "test_setup": {
            "cycler": "Biologic VMP3",
            "operator": "Jane Smith",
            "lab": "Battery Research Lab",
        },
        "notes": "Rate capability test at 2C",
    },
    "time_series": rate_test_time_series,
}

try:
    rate_test = client.cell_measurement.create(cell_instance.id, rate_test_data)
except Exception as e:
    if "duplicate" in str(e).lower() or "409" in str(e):
        # Find and delete existing measurement, then retry
        measurements = client.cell_measurement.list(cell_instance.id)
        for m in measurements:
            if m.name == rate_test_data["measurement"]["name"]:
                client.cell_measurement.delete(m.id)
                print(f"Deleted existing measurement: {m.name}")
                break
        rate_test = client.cell_measurement.create(cell_instance.id, rate_test_data)
    else:
        raise


print(f"\nCreated measurement: {rate_test.name}")

print("\n=== Summary ===")
print(f"Cell Specification: {cell_spec.name}")
print(f"Cell Instance: {cell_instance.name}")
print(f"Measurements: {measurement.name}, {rate_test.name}")
