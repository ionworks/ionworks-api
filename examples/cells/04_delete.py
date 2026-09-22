"""
Example: Deleting cell measurements, instances, and specifications.

This example demonstrates the deletion cascade:
- Deleting a measurement removes its steps and time series
- Deleting an instance removes all its measurements (and their data)
- Deleting a specification removes all instances (and their measurements)
"""

from ionworks import Ionworks

client = Ionworks()

# Configuration - these should exist from running 01_create.py
cell_spec_name = "NCM622/Graphite Coin Cell"

print("=== DELETING CELL DATA ===\n")

# Resolve spec name -> spec id
specs = client.cell_spec.list()
spec = next((s for s in specs if s.name == cell_spec_name), None)
if spec is None:
    print(f"Cell spec not found: {cell_spec_name}")
    print("Nothing to delete.")
    exit(0)

# Get first instance by ID
instances = client.cell_instance.list(spec.id)
instance = instances[0] if instances else None
if instance is None:
    print("No instances found.")

# Step 1: Delete a single measurement
if instance:
    try:
        measurements = client.cell_measurement.list(instance.id)
        if measurements:
            measurement = measurements[0]
            client.cell_measurement.delete(measurement.id)
            print(f"1. Deleted measurement: {measurement.name}")
            print("   (This also deleted associated steps and time series data)")
        else:
            print("1. No measurements found for this instance.")
    except Exception as e:
        print(f"1. Measurement not found or already deleted: {e}")

# Step 2: Delete a cell instance (cascades to all its measurements)
if instance:
    client.cell_instance.delete(instance.id)
    print(f"\n2. Deleted cell instance: {instance.name}")
    print("   (This also deleted all measurements, steps, and time series)")

# Step 3: Delete the cell specification (cascades to all instances)
# Note: This is commented out by default to preserve the spec for re-running examples
# Uncomment to fully clean up

# client.cell_spec.delete(spec.id)
# print(f"\n3. Deleted cell specification: {cell_spec_name}")
# print("   (This also deleted all instances, measurements, and data files)")

print("\n=== Cleanup Complete ===")
print("Note: Cell specification was preserved. Uncomment the delete call to remove it.")
print(f"Spec ID for manual deletion: {spec.id}")
