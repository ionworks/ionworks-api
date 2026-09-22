"""
Example: Reading cell specifications, instances, and measurements.

This example demonstrates various ways to retrieve cell data:
- Listing and filtering cell specifications
- Getting full nested data with components/materials
- Accessing cell instances and their measurements
- Working with time series and step data
"""

from ionworks import Ionworks

client = Ionworks()

# Configuration
cell_spec_name = "NCM622/Graphite Coin Cell"

# Resolve spec name -> spec id
specs = client.cell_spec.list()
spec = next((s for s in specs if s.name == cell_spec_name), None)
if spec is None:
    raise RuntimeError(f"Cell spec name not found: {cell_spec_name}")
cell_spec_id = spec.id

print("=== CELL SPECIFICATION OPERATIONS ===\n")

# 1a. List all cell specifications (metadata only)
print("1a. List all cell specifications (metadata only):")
all_specs = client.cell_spec.list()
for s in all_specs[:5]:  # Show first 5
    print(f"  - {s.name} (form_factor: {s.form_factor})")
print(f"  ... Total: {len(all_specs)} specifications")

# 1b. List with components (parallel get for each spec)
print("\n1b. List with components:")
specs_full = client.cell_spec.list(include_components=True)
for s in specs_full[:3]:
    components = []
    if getattr(s, "cathode", None):
        components.append(f"cathode={s.cathode['material']['name']}")
    if getattr(s, "anode", None):
        components.append(f"anode={s.anode['material']['name']}")
    print(f"  - {s.name}: {', '.join(components) or 'no components'}")
print()

# 2. Get full cell specification with nested components and materials
print("2. Get cell specification with components:")
full_spec = client.cell_spec.get(cell_spec_id)
print(f"  Name: {full_spec.name}")
print(f"  Form factor: {full_spec.form_factor}")
print(f"  Manufacturer: {full_spec.manufacturer}")

# Access ratings with units
print("\n  Ratings:")
cap = full_spec.ratings["capacity"]
print(f"    Capacity: {cap['value']} {cap['unit']}")
v_min = full_spec.ratings["voltage_min"]
v_max = full_spec.ratings["voltage_max"]
print(f"    Voltage range: {v_min['value']}-{v_max['value']} {v_max['unit']}")
if full_spec.ratings.get("nominal_voltage"):
    nom = full_spec.ratings["nominal_voltage"]
    print(f"    Nominal voltage: {nom['value']} {nom['unit']}")

# Access nested component data
if full_spec.cathode:
    print("\n  Cathode:")
    print(f"    Material: {full_spec.cathode['material']['name']}")
    if full_spec.cathode["material"].get("manufacturer"):
        print(f"    Manufacturer: {full_spec.cathode['material']['manufacturer']}")
    if full_spec.cathode.get("properties"):
        loading = full_spec.cathode["properties"].get("loading")
        if loading:
            print(f"    Loading: {loading['value']} {loading['unit']}")

if full_spec.anode:
    print("\n  Anode:")
    print(f"    Material: {full_spec.anode['material']['name']}")
    if full_spec.anode["material"].get("manufacturer"):
        print(f"    Manufacturer: {full_spec.anode['material']['manufacturer']}")

if full_spec.electrolyte:
    print("\n  Electrolyte:")
    print(f"    Material: {full_spec.electrolyte['material']['name']}")
    if full_spec.electrolyte["material"].get("definition"):
        defn = full_spec.electrolyte["material"]["definition"]
        if "salt" in defn:
            print(f"    Salt: {defn['salt']}")

# Access source/provenance
if full_spec.source:
    print("\n  Source:")
    for key, value in full_spec.source.items():
        print(f"    {key}: {value}")

print("\n" + "=" * 50)
print("=== SPEC-LEVEL DATA AGGREGATION ===\n")

# 3. List instances and measurements using flat endpoints
print("3. Instances and measurements (flat composition):")
instances = client.cell_instance.list(cell_spec_id)
print(f"  Number of instances: {len(instances)}")
all_measurements = []
for inst in instances:
    print(f"    - Instance: {inst.name} (batch: {inst.batch})")
    # Foreign key navigates to parent spec
    print(f"      cell_specification_id: {inst.cell_specification_id}")
    inst_measurements = client.cell_measurement.list(inst.id)
    all_measurements.extend(inst_measurements)
print(f"  Total measurements: {len(all_measurements)}")

print("\n" + "=" * 50)
print("=== INSTANCE-LEVEL OPERATIONS ===\n")

# 4. Get cell instance by ID
if not instances:
    raise RuntimeError("No instances found for spec")
cell_instance_id = instances[0].id
print("4. Get cell instance by ID:")
instance = client.cell_instance.get(cell_instance_id)
print(f"  Name: {instance.name}")
print(f"  Batch: {instance.batch}")
print(f"  Date manufactured: {instance.date_manufactured}")

# Access measured properties
if instance.measured_properties:
    print("  Measured properties:")
    for component, props in instance.measured_properties.items():
        if props:
            print(f"    {component}:")
            for prop_name, prop_value in props.items():
                if isinstance(prop_value, dict) and "value" in prop_value:
                    print(
                        f"      {prop_name}: {prop_value['value']} {prop_value['unit']}"
                    )
                else:
                    print(f"      {prop_name}: {prop_value}")

# 5. Get instance detail (composed from flat endpoints)
print("\n5. Cell instance detail (composed, parallel):")
instance_detail = client.cell_instance.detail(cell_instance_id)
print(f"  Specification ID: {instance_detail.specification_id}")
print(f"  Instance: {instance_detail.instance.name}")
print(f"  Number of measurements: {len(instance_detail.measurements)}")
for md in instance_detail.measurements:
    print(f"    - {md.name}")
    if md.steps is not None:
        print(f"      Steps shape: {md.steps.shape}")
    if md.cycles is not None:
        print(f"      Cycles shape: {md.cycles.shape}")
    if md.time_series is not None:
        print(f"      Time series shape: {md.time_series.shape}")

# 6. Instance detail with include flags (skip time series)
print("\n6. Instance detail (metadata only, no heavy data):")
light_detail = client.cell_instance.detail(
    cell_instance_id,
    include_steps=False,
    include_cycles=False,
    include_time_series=False,
)
print(f"  Measurements: {len(light_detail.measurements)}")
for md in light_detail.measurements:
    print(f"    - {md.name}")
    print(f"      steps=None: {md.steps is None}")
    print(f"      time_series=None: {md.time_series is None}")

print("\n" + "=" * 50)
print("=== MEASUREMENT-LEVEL OPERATIONS ===\n")

# 7. List measurements for an instance
print("7. List measurements for instance:")
measurements = client.cell_measurement.list(cell_instance_id)
print(f"  Found {len(measurements)} measurements")
for m in measurements:
    print(f"    - {m.name}")
    if m.protocol:
        print(f"      Protocol: {m.protocol.get('name')}")
        if m.protocol.get("ambient_temperature_degc"):
            print(f"      Temperature: {m.protocol['ambient_temperature_degc']} °C")
    if m.test_setup:
        if m.test_setup.get("cycler"):
            print(f"      Cycler: {m.test_setup['cycler']}")

# 8. Get measurement by ID
if not measurements:
    raise RuntimeError("No measurements found for instance")
cell_measurement_id = measurements[0].id
print("\n8. Get measurement by ID:")
measurement_by_id = client.cell_measurement.get(cell_measurement_id)
print(f"  Name: {measurement_by_id.name}")
print(f"  Protocol: {measurement_by_id.protocol}")
# Foreign key navigates to parent instance
print(f"  cell_instance_id: {measurement_by_id.cell_instance_id}")

# 9. Get full measurement detail (3 parallel requests + download)
# Uses flat endpoints: metadata, steps_and_cycles, signed URL
print("\n9. Single measurement detail:")
measurement_detail = client.cell_measurement.detail(cell_measurement_id)
print(f"  Name: {measurement_detail.name}")
print(f"  Instance ID: {measurement_detail.instance_id}")
print(f"  Steps shape: {measurement_detail.steps.shape}")
print(f"  Cycles shape: {measurement_detail.cycles.shape}")
print(f"  Time series shape: {measurement_detail.time_series.shape}")
print(f"  Time series columns: {list(measurement_detail.time_series.columns)}")

# Show sample of time series data
print("\n  Sample time series data (first 3 rows):")
print(measurement_detail.time_series.head(3))

# Show step summary
print("\n  Step summary:")
print(measurement_detail.steps[["Step count", "Step type", "Duration [s]"]])

# 10. Individual data access (steps, cycles, time series separately)
print("\n10. Individual data access:")
steps_df = client.cell_measurement.steps(cell_measurement_id)
print(f"  Steps shape: {steps_df.shape}")

cycles_df = client.cell_measurement.cycles(cell_measurement_id)
print(f"  Cycles shape: {cycles_df.shape}")

# steps_and_cycles is more efficient when you need both
sc = client.cell_measurement.steps_and_cycles(cell_measurement_id)
print(f"  Steps+Cycles (one call): {sc.steps.shape}, {sc.cycles.shape}")

ts_df = client.cell_measurement.time_series(cell_measurement_id)
print(f"  Time series shape: {ts_df.shape}")
