"""
Example: Updating cell specifications, instances, and measurements.

This example demonstrates how to update existing cell data:
- Updating cell specification with nested component/material data
- Updating cell instance measured properties
- Updating measurement metadata
"""

from ionworks import Ionworks

client = Ionworks()

# Configuration - these should exist from running 01_create.py
cell_spec_name = "NCM622/Graphite Coin Cell"

# Resolve spec name -> spec id
specs = client.cell_spec.list()
spec = next((s for s in specs if s.name == cell_spec_name), None)
if spec is None:
    raise RuntimeError(f"Cell spec name not found: {cell_spec_name}")
cell_spec_id = spec.id

print("=== UPDATING CELL SPECIFICATION ===\n")

# Update cell specification with nested component/material data
# Only provided fields will be updated - this is a partial update
updated_spec = client.cell_spec.update(
    cell_spec_id,
    {
        # Update ratings
        "ratings": {
            "capacity": {"value": 0.0022, "unit": "A.h"},  # Updated capacity
            "voltage_min": {"value": 2.5, "unit": "V"},
            "voltage_max": {"value": 4.2, "unit": "V"},
            "max_discharge_rate": {"value": 3, "unit": "C"},  # Updated max rate
        },
        # Update cathode component (material will be upserted)
        "cathode": {
            "properties": {
                "diameter": {"value": 14, "unit": "mm"},
                "thickness": {"value": 55, "unit": "um"},  # Updated thickness
                "loading": {"value": 13.0, "unit": "mg.cm-2"},  # Updated loading
                "porosity": {"value": 28, "unit": "percent"},  # Updated porosity
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
                    "tap_density": {"value": 2.5, "unit": "g.cm-3"},  # Added field
                },
            },
        },
        # Update source info
        "source": {
            "creator_name": "Battery Lab - Updated",
            "creator_orcid": "0000-0001-2345-6789",
            "publication_date": "2024-06-15",
            "license": "CC-BY-4.0",
            "doi": "10.1234/example.2024.001",  # Added DOI
        },
        # Update notes
        "notes": "Updated: Optimized cathode loading for improved rate capability",
    },
)

print(f"Updated cell specification: {updated_spec.name}")
cap = updated_spec.ratings["capacity"]
print(f"  New capacity: {cap['value']} {cap['unit']}")
max_rate = updated_spec.ratings["max_discharge_rate"]
print(f"  New max discharge rate: {max_rate['value']} {max_rate['unit']}")
if updated_spec.cathode:
    loading = updated_spec.cathode["properties"].get("loading")
    if loading:
        print(f"  New cathode loading: {loading['value']} {loading['unit']}")
print(f"  Notes: {updated_spec.notes}")

print("\n=== UPDATING CELL INSTANCE ===\n")

# Get instance by ID (use first instance for this example)
instances = client.cell_instance.list(cell_spec_id)
if not instances:
    raise RuntimeError("No instances found for spec")
cell_instance_id = instances[0].id

# Update cell instance with new measured properties
updated_instance = client.cell_instance.update(
    cell_instance_id,
    {
        # Update measured properties (as-built values from QC)
        "measured_properties": {
            "cathode": {
                "loading": {"value": 12.8, "unit": "mg.cm-2"},  # Updated
                "thickness": {"value": 54, "unit": "um"},  # Updated
                "porosity": {"value": 29, "unit": "percent"},  # Added
            },
            "anode": {
                "loading": {"value": 6.5, "unit": "mg.cm-2"},
                "thickness": {"value": 45, "unit": "um"},
                "porosity": {"value": 36, "unit": "percent"},  # Added
            },
            "cell": {
                "initial_ocv": {"value": 3.05, "unit": "V"},  # Updated
                "weight": {"value": 3.18, "unit": "g"},  # Updated
                "initial_impedance": {"value": 15.2, "unit": "ohm"},  # Added
            },
        },
        "notes": "Updated with post-rest OCV and impedance measurements",
    },
)

print(f"Updated cell instance: {updated_instance.name}")
print(f"  Notes: {updated_instance.notes}")
if updated_instance.measured_properties:
    cell_props = updated_instance.measured_properties.get("cell", {})
    if cell_props:
        print("  Cell measurements:")
        for prop_name, prop_value in cell_props.items():
            if isinstance(prop_value, dict) and "value" in prop_value:
                print(f"    {prop_name}: {prop_value['value']} {prop_value['unit']}")

print("\n=== UPDATING CELL MEASUREMENT ===\n")

# Get measurement by ID (use first measurement for this example)
measurements = client.cell_measurement.list(cell_instance_id)
if not measurements:
    raise RuntimeError("No measurements found for instance")
cell_measurement_id = measurements[0].id

# Update measurement metadata
updated_measurement = client.cell_measurement.update(
    cell_measurement_id,
    {
        "protocol": {
            "name": "CC-CV charge at C/10 to 4.2V (50mA cutoff), "
            "CC discharge at C/10 to 2.5V",
            "ambient_temperature_degc": 25.3,  # More precise
        },
        "test_setup": {
            "operator": "Jane Smith",
            "lab": "Battery Research Lab - Building A",
        },
        "notes": "Formation cycle completed successfully - high coulombic efficiency",
    },
)

print(f"Updated measurement: {updated_measurement.name}")
if updated_measurement.protocol:
    print(f"  Protocol: {updated_measurement.protocol.get('name')}")
    if updated_measurement.protocol.get("ambient_temperature_degc"):
        temp = updated_measurement.protocol["ambient_temperature_degc"]
        print(f"  Temperature: {temp} °C")
print(f"  Notes: {updated_measurement.notes}")

print("\n=== Summary ===")
print("Successfully updated:")
print(f"  - Cell Specification: {updated_spec.name}")
print(f"  - Cell Instance: {updated_instance.name}")
print(f"  - Measurement: {updated_measurement.name}")
